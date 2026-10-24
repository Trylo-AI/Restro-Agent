from datetime import UTC, datetime

from app.db.repositories.inbound import InboundMessage
from app.domain.conversation.pipeline import split_turns


def msg(i: int, type_: str, **payload: object) -> InboundMessage:
    return InboundMessage(id=f"m{i}", wamid=f"w{i}", type=type_, payload={"type": type_, **payload},
                          received_at=datetime(2026, 10, 24, 12, 0, i, tzinfo=UTC))


def test_texts_join_and_taps_stand_alone() -> None:
    batch = [
        msg(1, "text", text={"body": "hi"}),
        msg(2, "text", text={"body": "i want"}),
        msg(3, "text", text={"body": "2 cold coffee"}),
        msg(4, "interactive", interactive={"type": "button_reply", "button_reply": {"id": "confirm:3", "title": "Confirm"}}),
        msg(5, "image"),
        msg(6, "text", text={"body": "thanks"}),
    ]
    turns = split_turns(batch)
    assert [t.kind for t in turns] == ["text", "tap", "other", "text"]
    assert turns[0].text == "hi\ni want\n2 cold coffee" and turns[0].first_id == "m1"
    assert turns[1].reply_id == "confirm:3" and turns[1].reply_title == "Confirm"
    assert turns[3].text == "thanks"
