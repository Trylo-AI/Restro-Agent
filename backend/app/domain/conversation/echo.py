"""TEMPORARY (S6 only): echo every turn so the simulator can be built and tested. Replaced in S7."""

from app.domain.conversation.turn import Reply, Turn, TurnContext, TurnResult
from app.integrations.whatsapp import messages as m


async def echo(ctx: TurnContext, turn: Turn) -> TurnResult:
    if turn.kind == "tap":
        return TurnResult(replies=[Reply(m.Text(f"You tapped: {turn.reply_title or turn.reply_id}"))])
    if turn.kind == "other":
        return TurnResult(replies=[Reply(m.Text(f"You sent: {turn.messages[0].type}"))])
    return TurnResult(replies=[
        Reply(m.Text(f"You said: {turn.text}")),
        Reply(m.Buttons("Echo mode (S6). Try a button:", [m.Button("menu", "Menu"), m.Button("help", "Call waiter")])),
        Reply(m.ListMessage("And a list:", "See options", [m.Section("Cold coffee", [
            m.Row("item:demo-1", "Cold Coffee", "₹ 190 · veg"), m.Row("item:demo-2", "Cold Brew Tonic", "₹ 240 · veg"),
        ])])),
    ])
