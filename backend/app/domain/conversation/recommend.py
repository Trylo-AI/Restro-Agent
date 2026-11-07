"""Recommend, mood and "Other ideas" taps, and typed moods (docs/backend.md#recommendations).

Tap: code candidates → one `recommend` call (or code picks: ≤ 3 candidates, kill switch `handoff`, the per-visit cap,
any failure) → merge and top up → re-check availability and allergens → drop model text naming a dish not shown →
render. A typed mood rides the turn call:
its picks are re-filtered and topped up, with no second call. Never a handoff, never a clarify strike. The picks
live in `state.rec` (ids only). Customer actions: no audit.
"""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.repositories import menu as menu_repo
from app.db.tx import conn
from app.domain import recommend
from app.domain.ai_agent import calls, guardrails
from app.domain.ai_agent import recommend as ai_recommend
from app.domain.ai_agent.interpret import Interpretation
from app.domain.conversation import flows, render
from app.domain.conversation.turn import Reply, Turn, TurnContext, TurnResult
from app.domain.recommend import MOODS, CandidateSet, Pick, RecContext
from app.ops import tracing

log = get_logger(__name__)


def _intro(mood: str | None, first_name: str | None, *, fallback: bool) -> str:
    if mood in MOODS:
        return f"For {MOODS[mood][1]}, try:"
    if fallback:
        return "Here are a few favourites:"
    return f"Here are a few picks for you, {first_name}:" if first_name else "Here are a few picks for you:"


async def _final(ctx: TurnContext, rc: RecContext, cs: CandidateSet, llm: list[Pick], mood: str | None,
                 exclude: frozenset[str]) -> tuple[list[Pick], CandidateSet]:
    """Re-reads the candidates (a dish may have sold out or changed while the model thought), re-filters with the
    fresh allergen states, keeps the still-valid model picks and tops up from the code ranking."""
    ids = [it.id for it in cs.items]
    async with conn() as c:
        fresh = await menu_repo.items_by_ids(c, ctx.cafe.id, ids)
        states = await menu_repo.allergens(c, ctx.cafe.id, ids)
    rc2 = replace(rc, items=[fresh[i] for i in ids if i in fresh], allergen_states=states)
    cs2 = recommend.filter_candidates(rc2, exclude=exclude)
    cs2.allergy_filtered = cs.allergy_filtered
    by_id = {it.id: it for it in cs2.items}
    kept = [Pick(by_id[p.item.id], p.why, p.source) for p in llm if p.item.id in by_id]
    fallback = recommend.fallback_rank(rc2, cs2, mood=mood, taken=[p.item.id for p in kept])
    return recommend.merge(kept, cs2, fallback), cs2


def _scrub(rc: RecContext, picks: list[Pick], intro: str | None) -> tuple[list[Pick], str | None]:
    """Last check on the final picks: model text that names a dish not shown (filtered out, in the cart, dropped on
    the re-read, or just not picked) goes, and code writes the why or the intro instead (invariant 2)."""
    shown = [p.item for p in picks]
    out = []
    for p in picks:
        if p.source == "llm" and recommend.names_other_dish(p.why, rc.items, shown):
            tracing.degraded("recommend_pick_dropped", why="names_dish")
            p = Pick(p.item, recommend.code_why(rc, p.item), p.source)
        out.append(p)
    return out, (None if recommend.names_other_dish(intro, rc.items, shown) else intro)


def _reply(ctx: TurnContext, state: dict[str, Any], picks: list[Pick], cs: CandidateSet, intro: str,
           mood: str | None) -> list[Reply]:
    editing = state.get("order_edit")
    more = bool(cs.ids - {p.item.id for p in picks})
    msg = render.recommendations([(p.item.id, p.item.name, p.item.price_paise, p.item.veg, p.why) for p in picks],
                                 intro, mood=mood, more=more, allergy=cs.allergy_filtered,
                                 editing=int(editing["number"]) if editing and editing.get("number") else None)
    return [Reply(msg)]


def _remember(state: dict[str, Any], picks: list[Pick], *, shown: list[str], mood: str | None, mood_text: str | None,
              llm_calls: int) -> None:
    state["rec"] = {"shown": (shown + [p.item.id for p in picks])[-recommend.MAX_SHOWN:], "mood": mood,
                    "mood_text": mood_text, "llm_calls": llm_calls, "at": datetime.now(UTC).isoformat()}
    state["clarify_strikes"] = 0


async def _rank(ctx: TurnContext, turn: Turn, rc: RecContext, cs: CandidateSet, phrase: str | None,
                shown: list[str]) -> tuple[list[Pick], str | None]:
    assert rc.view is not None
    text = ai_recommend.suffix(rc, cs, mood=phrase, shown=shown)
    attempted = await ai_recommend.rank(cafe_id=ctx.cafe.id, cafe_model=ctx.cafe.settings.get("model"),
                                        conversation_id=ctx.conversation.id, message_id=turn.first_id, view=rc.view,
                                        language=rc.language, prompt_text=text)
    if attempted is None:
        return [], None  # calls.call recorded recommend_fallback
    ranked = ai_recommend.validate(attempted.result.data, rc.view, cs.ids)
    await calls.add_guardrails_to_trace(ctx.cafe.id, attempted.call_id, ranked.guardrails)
    if not ranked.picks:
        tracing.degraded("recommend_fallback", why="no_valid_picks")
    return [Pick(item, why or recommend.code_why(rc, item), "llm") for item, why in ranked.picks], ranked.intro


async def on_tap(ctx: TurnContext, state: dict[str, Any], turn: Turn, action: str, arg: str | None) -> TurnResult:
    """`recommend` · `rec_mood:<code>` · `rec_more`. The router has already sent paused / closed their fixed reply."""
    rec = dict(state.get("rec") or {})
    more = action == "rec_more"
    if action == "rec_mood":
        mood, mood_text = (arg if arg in MOODS else None), None
    elif more:
        mood, mood_text = rec.get("mood"), rec.get("mood_text")
    else:
        mood, mood_text = None, None
    shown = [str(x) for x in rec.get("shown") or []] if more else []
    llm_calls = int(rec.get("llm_calls") or 0)

    rc = await recommend.load_context(ctx, state)
    if not rc.items:
        return TurnResult(state=state, replies=[Reply(render.paused())])
    exclude = frozenset(shown)
    cs = recommend.filter_candidates(rc, exclude=exclude)
    if not cs.items:
        state["clarify_strikes"] = 0
        if more:
            return TurnResult(state=state, replies=[Reply(render.no_more_picks())] + await flows.menu_categories(ctx))
        return TurnResult(state=state, replies=[Reply(render.no_safe_picks(cs.allergy_filtered))])

    llm: list[Pick] = []
    intro: str | None = None
    called = 0
    if len(cs.items) <= recommend.MAX_PICKS:
        pass  # nothing to rank: the candidates are the picks
    elif calls.kill_switch() == "handoff":
        tracing.degraded("recommend_fallback", why="kill_switch")
    elif llm_calls >= get_settings().recommend_llm_per_visit:
        tracing.degraded("recommend_capped", calls=llm_calls)
    else:
        called = 1
        phrase = MOODS[mood][1] if mood in MOODS else recommend.sanitise_mood(mood_text)
        llm, intro = await _rank(ctx, turn, rc, cs, phrase, shown)

    boost = mood or recommend.guess_mood(mood_text)
    picks, cs2 = await _final(ctx, rc, cs, llm, boost, exclude)
    if not picks:
        state["clarify_strikes"] = 0
        return TurnResult(state=state, replies=[Reply(render.no_safe_picks(cs.allergy_filtered))])
    fallback = len(cs.items) > recommend.MAX_PICKS and not any(p.source == "llm" for p in picks)
    picks, intro = _scrub(rc, picks, intro)
    intro = (None if fallback else intro) or _intro(mood, rc.first_name, fallback=fallback)  # no model picks: code intro
    _remember(state, picks, shown=shown, mood=mood, mood_text=mood_text, llm_calls=llm_calls + called)
    tracing.count(rec_shown=len(picks), rec_llm=called)
    log.info("recommend", cafe_id=ctx.cafe.id, conversation_id=ctx.conversation.id, via=action,
             items=[p.item.id for p in picks], sources=[p.source for p in picks], candidates=len(cs.items),
             excluded=len(cs.excluded), llm=called)
    return TurnResult(state=state, replies=_reply(ctx, state, picks, cs2, intro, mood))


async def from_turn(ctx: TurnContext, state: dict[str, Any], it: Interpretation, *, with_intro: bool = True) -> TurnResult:
    """Intent `recommend` from the turn call: no second call. The model's picks are kept only when they are still
    candidates; each `why` already passed clean_why (else code writes one); topped up by the code ranking."""
    ask = it.recommend
    rc = await recommend.load_context(ctx, state)
    if not rc.items:
        return TurnResult(state=state, replies=[Reply(render.paused())])
    cs = recommend.filter_candidates(rc)
    state["clarify_strikes"] = 0
    if not cs.items:
        return TurnResult(state=state, replies=[Reply(render.no_safe_picks(cs.allergy_filtered))])
    raw = ask.picks if ask else []
    llm = [Pick(p.item, p.why or recommend.code_why(rc, p.item), "llm") for p in raw if p.item.id in cs.ids]
    if len(llm) < len(raw):
        tracing.degraded("recommend_pick_dropped", why="filtered", picks=len(raw) - len(llm))
    mood_text = ask.mood if ask else None
    picks, cs2 = await _final(ctx, rc, cs, llm, recommend.guess_mood(mood_text), frozenset())
    if not picks:
        return TurnResult(state=state, replies=[Reply(render.no_safe_picks(cs.allergy_filtered))])
    picks, intro = _scrub(rc, picks, guardrails.clean_why(it.reply, ai_recommend.INTRO_MAX) if with_intro else None)
    intro = intro or _intro(None, rc.first_name, fallback=False)
    rec = dict(state.get("rec") or {})
    _remember(state, picks, shown=[], mood=None, mood_text=mood_text, llm_calls=int(rec.get("llm_calls") or 0))
    tracing.count(rec_shown=len(picks), rec_llm=0)
    log.info("recommend", cafe_id=ctx.cafe.id, conversation_id=ctx.conversation.id, via="turn",
             items=[p.item.id for p in picks], sources=[p.source for p in picks], candidates=len(cs.items),
             excluded=len(cs.excluded), llm=0)
    return TurnResult(state=state, replies=_reply(ctx, state, picks, cs2, intro, None))
