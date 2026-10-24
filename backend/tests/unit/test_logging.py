import json

from app.core.logging import configure_logging, get_logger


def test_forbidden_fields_are_scrubbed(capsys) -> None:  # type: ignore[no-untyped-def]
    configure_logging("info", service="test")
    get_logger().info("turn_done", conversation_id="c1", wa_id="919999000001", text="2 cold coffee")
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["conversation_id"] == "c1"
    assert line["wa_id"] == "[removed]"
    assert line["text"] == "[removed]"
