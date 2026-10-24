// Customer profile: at-a-glance box, then feedback, complaints, recent orders, comments
// (docs/console.md#customer-profile). Shared by Live orders, Calls and bills, and Chats.
import { useQueryClient } from "@tanstack/react-query";
import { CalendarClock, CircleAlert, Heart, Leaf, Phone, ThumbsDown, UtensilsCrossed, X } from "lucide-react";
import { useState, type ReactNode } from "react";

import { useNow } from "../../app/alerts";
import { useToasts } from "../../app/toasts";
import { Empty, Sheet } from "../../components/ui";
import { ApiError } from "../../lib/api/client";
import { useCustomerProfile, type CustomerProfile } from "../../lib/data";
import { dayMonth as day, inr, ordinal, relative } from "../../lib/format";
import { customers } from "../../lib/mutations";
import { useSession } from "../../lib/session";
import type { CustomerNote, Preference } from "../../lib/types";

const KIND_LABEL: Record<Preference["kind"], string> = {
  diet: "Diet", milk: "Milk", sugar: "Sugar", spice: "Spice", seating: "Seating",
  allergy: "Allergy", dislike: "Dislikes", like: "Likes", other: "Note",
};
const PREFERS: Preference["kind"][] = ["milk", "sugar", "spice", "seating", "like", "other"];
const SEGMENT: Record<string, string> = { new: "New", regular: "Regular", lapsed: "Lapsed" };


export function CustomerProfilePanel({ customerId, onClose }: { customerId: string; onClose: () => void }) {
  return (
    <Sheet title="Customer profile" onClose={onClose}>
      <CustomerProfileBody customerId={customerId} />
    </Sheet>
  );
}

export function CustomerProfileBody({ customerId }: { customerId: string }) {
  const profile = useCustomerProfile(customerId);
  if (profile.isPending) return <Empty>Loading profile…</Empty>;
  if (!profile.data?.card) {
    return profile.isError
      ? <Empty>Couldn't load this profile. <button className="btn small" onClick={() => void profile.refetch()}>Try again</button></Empty>
      : <Empty>No profile for this customer.</Empty>;
  }
  return (
    <div className="profile">
      <AtAGlance p={profile.data} customerId={customerId} />
      <Details p={profile.data} />
      <Comments p={profile.data} customerId={customerId} />
    </div>
  );
}

function useRefresh() {
  const qc = useQueryClient();
  const { cafeId } = useSession();
  return (customerId: string) => {
    void qc.invalidateQueries({ queryKey: ["profile", cafeId, customerId] });
    void qc.invalidateQueries({ queryKey: ["orders", cafeId] }); // "On profile" allergy on order cards
  };
}

function AtAGlance({ p, customerId }: { p: CustomerProfile; customerId: string }) {
  const now = useNow(60_000);
  const toasts = useToasts();
  const refresh = useRefresh();
  const card = p.card!;
  const of = (k: Preference["kind"]) => p.prefs.filter((x) => x.kind === k);

  async function remove(pref: Preference) {
    try {
      await customers.removePreference(customerId, pref.kind, pref.value);
      toasts.show(`Removed ${KIND_LABEL[pref.kind].toLowerCase()}: ${pref.value}`);
    } catch (e) {
      toasts.show(e instanceof Error ? e.message : "Couldn't remove it", true);
    } finally {
      refresh(customerId);
    }
  }

  const chip = (pref: Preference, cls: string, icon: ReactNode, label?: string) => (
    <span key={pref.id} className={`pchip ${cls}`}>
      {icon} {label ? `${label}: ` : ""}{pref.value}
      <button aria-label={`Remove ${pref.value}`} onClick={() => void remove(pref)}><X size={13} /></button>
    </span>
  );
  const prefers = PREFERS.flatMap((k) => of(k));

  return (
    <section className="glance" aria-label="At a glance">
      <div className="glance-name">
        {card.first_name || "Guest"} <span>{card.visits > 1 ? `${ordinal(card.visits)} visit` : "First visit"}</span>
      </div>
      <div className="glance-line"><Phone size={16} aria-hidden /> {card.masked_phone}
        {card.segment && <span className="chip flag">{SEGMENT[card.segment]}</span>}</div>
      <div className="glance-line"><CalendarClock size={16} aria-hidden />
        {card.last_visit_at ? `Last visit ${day(card.last_visit_at)} · ${relative(card.last_visit_at, now)}` : "No visit yet"}</div>
      {card.top_items?.length ? (
        <div className="glance-line"><UtensilsCrossed size={16} aria-hidden /> <span className="grow">Usually: {card.top_items.join(", ")}</span></div>
      ) : null}
      {of("allergy").length > 0 && (
        <div className="glance-allergy">
          <CircleAlert size={18} aria-hidden /> <strong>Allergy</strong>
          <div className="pchips">{of("allergy").map((x) => chip(x, "allergy", null))}</div>
        </div>
      )}
      {of("dislike").length > 0 && (
        <div className="glance-row"><span className="glance-label"><ThumbsDown size={15} aria-hidden /> Dislikes</span>
          <div className="pchips">{of("dislike").map((x) => chip(x, "dislike", null))}</div></div>
      )}
      {of("diet").length > 0 && (
        <div className="glance-row"><span className="glance-label"><Leaf size={15} aria-hidden /> Diet</span>
          <div className="pchips">{of("diet").map((x) => chip(x, "diet", null))}</div></div>
      )}
      {prefers.length > 0 && (
        <div className="glance-row"><span className="glance-label"><Heart size={15} aria-hidden /> Prefers</span>
          <div className="pchips">{prefers.map((x) => chip(x, "pref", null, KIND_LABEL[x.kind]))}</div></div>
      )}
      {p.prefs.length === 0 && (
        <div className="glance-line muted">No preferences yet. Add a comment below when the customer tells you something.</div>
      )}
    </section>
  );
}

function stars(n: number): string {
  return "★".repeat(n) + "☆".repeat(5 - n);
}

function Details({ p }: { p: CustomerProfile }) {
  const card = p.card!;
  return (
    <>
      <section className="pblock">
        <h3>Feedback {card.rating_count > 0 && <span className="muted">· {card.avg_rating}★ average · {card.rating_count} rating{card.rating_count > 1 ? "s" : ""}</span>}</h3>
        {p.feedback.length ? (
          <ul className="plist">
            {p.feedback.map((f) => (
              <li key={f.id}><span className="stars" aria-label={`${f.rating} of 5`}>{stars(f.rating)}</span> <span className="muted">#{f.number} · {day(f.created_at)}</span>
                {f.text && <div className="words">“{f.text}”</div>}</li>
            ))}
          </ul>
        ) : <p className="muted">No ratings yet.</p>}
      </section>
      <section className="pblock">
        <h3>Complaints</h3>
        {p.complaints.length ? (
          <ul className="plist">
            {p.complaints.map((c) => (
              <li key={c.id}><strong>{c.label}</strong> <span className="muted">· {day(c.at)}</span>
                {c.words && <div className="words">“{c.words}”</div>}</li>
            ))}
          </ul>
        ) : <p className="muted">No complaints.</p>}
      </section>
      <section className="pblock">
        <h3>Recent orders</h3>
        {p.recent.length ? (
          <ul className="plist">
            {p.recent.map((o) => (
              <li key={o.id} className={o.status === "CANCELLED" ? "struck" : ""}>
                <span className="mono">#{o.number}</span> <span className="muted">· {day(o.created_at)} · {inr(o.total_paise)}</span>
                {o.status === "CANCELLED" && <span className="sr-only"> (cancelled)</span>}
                <div>{o.items.map((i) => `${i.qty} × ${i.name_snapshot}`).join(", ")}</div>
              </li>
            ))}
          </ul>
        ) : <p className="muted">No orders yet.</p>}
      </section>
    </>
  );
}

function opText(op: CustomerNote["ai_ops"][number]): string {
  const kind = KIND_LABEL[op.kind as Preference["kind"]] ?? op.kind;
  if (op.op === "remove") return `Removed ${kind.toLowerCase()}: ${op.value}`;
  if (op.op === "set") return `${kind}: ${op.value}${op.previous ? ` (was ${op.previous})` : ""}`;
  return `Added ${kind.toLowerCase()}: ${op.value}`;
}

function Comments({ p, customerId }: { p: CustomerProfile; customerId: string }) {
  const now = useNow(30_000);
  const toasts = useToasts();
  const refresh = useRefresh();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await customers.addNote(customerId, text.trim());
      setText("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save. Try again.");
    } finally {
      setBusy(false);
      refresh(customerId);
    }
  }

  async function undo(n: CustomerNote) {
    try {
      await customers.undoNote(n.id);
      toasts.show("Profile changes undone");
    } catch (e) {
      toasts.show(e instanceof ApiError && e.status === 409
        ? "Changed again since — remove it from the box instead"
        : e instanceof Error ? e.message : "Couldn't undo", true);
    } finally {
      refresh(customerId);
    }
  }

  return (
    <section className="pblock">
      <h3>Comments</h3>
      <div className="add-comment">
        <label className="field"><span className="sr-only">Add a comment</span>
          <textarea maxLength={500} value={text} onChange={(e) => setText(e.target.value)}
            placeholder="e.g. Allergic to peanuts, likes oat milk, prefers the window seat" />
        </label>
        <div className="row">
          <span className="hint grow">Write what the customer told you — the profile updates itself. Offers and opt-outs can only be changed by the customer.</span>
          <button className="btn primary small" disabled={!text.trim() || busy} onClick={() => void save()}>Save</button>
        </div>
        {error && <div className="error-text">{error}</div>}
      </div>
      {p.notes.length ? (
        <ul className="plist notes">
          {p.notes.map((n) => (
            <li key={n.id}>
              <div className="muted">{n.author} · {relative(n.created_at, now)}</div>
              <div className="note-text">{n.text}</div>
              <NoteResult n={n} onUndo={() => void undo(n)} />
            </li>
          ))}
        </ul>
      ) : <p className="muted">No comments yet.</p>}
    </section>
  );
}

function NoteResult({ n, onUndo }: { n: CustomerNote; onUndo: () => void }) {
  if (n.ai_status === "pending") return <div className="note-result pending">Updating profile…</div>;
  if (n.ai_status === "failed") return <div className="note-result">Couldn't read this — profile unchanged</div>;
  if (n.ai_status === "no_changes" || !n.ai_ops.length) return <div className="note-result">No profile changes</div>;
  return (
    <div className="note-result applied">
      <ul>{n.ai_ops.map((op, i) => <li key={i} className={n.undone_at ? "struck" : ""}>{opText(op)}</li>)}</ul>
      {n.undone_at ? <span className="muted">Undone</span> : <button className="btn small" onClick={onUndo}>Undo</button>}
    </div>
  );
}
