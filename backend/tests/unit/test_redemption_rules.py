"""Offer codes: one verdict for the table bill and the counter, café-local expiry, discount maths, the query kinds,
the offer message date and the recipients CSV (docs/rules.md Redemption row)."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.core.errors import BadRequest
from app.db.repositories.campaigns import Campaign
from app.db.repositories.redemptions import CodeInfo, Redemption
from app.domain import campaigns
from app.domain.campaigns import export
from app.domain.campaigns import redemption as r

IST = "Asia/Kolkata"


def info(**kw: Any) -> CodeInfo:
    base: dict[str, Any] = dict(
        recipient_id="rec-1", campaign_id="cam-1", status="delivered", valid_until=None, discount_percent=20,
        campaign_status="sending", customer_id="cus-1", code="BACK20-7KQ", campaign_name="Weekend comeback",
        offer_text="20% off your next visit", sent_at=None, consent="opted_in", timezone=IST)
    return CodeInfo(**{**base, **kw})


def used(**kw: Any) -> Redemption:
    base: dict[str, Any] = dict(id="red-1", code="BACK20-7KQ", table_session_id=None, order_number=None,
                                discount_paise=24800, redeemed_at=datetime(2026, 10, 12, 7, 11, tzinfo=UTC),
                                channel="counter", bill_paise=124000, redeemed_by_name="Ravi", table_label=None)
    return Redemption(**{**base, **kw})


NOW = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)  # 14:30 IST


def test_verdict_not_found_when_no_code() -> None:
    assert r.verdict(None, None, NOW, IST) == "not_found"
    assert r.explain("not_found", None, None, NOW, IST).reason == "This isn't a valid code, or it has expired."


def test_verdict_redeemed_wins_over_expired_and_cancelled() -> None:
    old = info(valid_until=NOW - timedelta(days=10), campaign_status="cancelled", status="failed")
    assert r.verdict(old, used(), NOW, IST) == "redeemed"


def test_verdict_campaign_cancelled() -> None:
    v = r.verdict(info(campaign_status="cancelled"), None, NOW, IST)
    assert v == "campaign_cancelled"
    assert r.explain(v, info(campaign_status="cancelled"), None, NOW, IST).reason == "The café cancelled this offer."


def test_verdict_queued_and_deferred_are_not_sent() -> None:
    for status in ("queued", "deferred"):
        assert r.verdict(info(status=status), None, NOW, IST) == "not_sent"
    assert r.explain("not_sent", info(status="queued"), None, NOW, IST).reason == \
        "Not sent yet: the customer hasn't received this code."


def test_verdict_failed_is_not_delivered() -> None:
    assert r.verdict(info(status="failed"), None, NOW, IST) == "not_delivered"
    assert r.explain("not_delivered", info(status="failed"), None, NOW, IST).reason == \
        "This message failed: the customer never got the code."


def test_verdict_valid_through_end_of_cafe_local_day() -> None:
    # valid_until 12 Oct 15:42 IST (the moment the draft was saved + N days)
    until = datetime(2026, 10, 12, 10, 12, tzinfo=UTC)
    code = info(valid_until=until)
    assert r.verdict(code, None, datetime(2026, 10, 12, 18, 29, tzinfo=UTC), IST) == "eligible"  # 23:59 IST
    late = datetime(2026, 10, 12, 18, 30, tzinfo=UTC)  # 13 Oct 00:00 IST
    assert r.verdict(code, None, late, IST) == "expired"
    assert r.explain("expired", code, None, late, IST).reason == "Expired on 12 Oct."
    assert r.verdict(info(valid_until=NOW - timedelta(days=1)), None, NOW, IST) == "expired"  # the old "1 day ago"


def test_verdict_utc_evening_valid_until_uses_local_date() -> None:
    until = datetime(2026, 10, 12, 20, 0, tzinfo=UTC)  # 13 Oct 01:30 IST
    code = info(valid_until=until)
    assert r.verdict(code, None, datetime(2026, 10, 13, 18, 0, tzinfo=UTC), IST) == "eligible"  # 13 Oct 23:30 IST
    assert r.verdict(code, None, datetime(2026, 10, 13, 18, 30, tzinfo=UTC), IST) == "expired"  # 14 Oct 00:00 IST
    assert "valid till 13 Oct" in r.explain("eligible", code, None, NOW, IST).reason


def test_verdict_null_valid_until_never_expires() -> None:
    code = info(valid_until=None)
    assert r.verdict(code, None, NOW + timedelta(days=3650), IST) == "eligible"
    assert r.explain("eligible", code, None, NOW, IST).reason == "20 % off the bill"


def test_verdict_held_is_eligible_with_warning() -> None:
    code = info(status="held", consent="opted_out")
    assert r.verdict(code, None, NOW, IST) == "eligible"
    check = r.explain("eligible", code, None, NOW, IST)
    assert check.warning == "WhatsApp held this message; the customer may not have seen it."  # the first that applies
    recent = used(code="OTHER-AAA")
    check = r.explain("eligible", info(consent="opted_out"), None, NOW, IST, recent=recent, name="Riya Sharma")
    assert check.warning == "Riya used OTHER-AAA at 12:41. One offer per visit at the counter."
    assert r.explain("eligible", info(consent="opted_out"), None, NOW, IST).warning == \
        "Opted out of offers since; this code still works."
    no_percent = r.explain("eligible", info(discount_percent=None, offer_text="a free cookie"), None, NOW, IST)
    assert no_percent.reason == "Offer: a free cookie · no bill discount" and no_percent.warning is None


def test_explain_redeemed_counter_and_table_bill_texts_in_cafe_timezone() -> None:
    counter = used(redeemed_at=datetime(2026, 10, 12, 7, 11, tzinfo=UTC))  # 12:41 IST, same café day as NOW
    assert r.explain("redeemed", info(), counter, NOW, IST).reason == "Used today at 12:41 at the counter by Ravi"
    bill = used(channel="table_bill", table_label="T7", order_number=1041, redeemed_by_name=None,
                redeemed_at=datetime(2026, 10, 11, 19, 0, tzinfo=UTC))  # 12 Oct 00:30 IST: "today" in the café
    assert r.explain("redeemed", info(), bill, NOW, IST).reason == "Used today at 00:30 on the T7 bill (#1041)"
    earlier = used(channel="table_bill", table_label="T7", order_number=1041, redeemed_by_name=None,
                   redeemed_at=datetime(2026, 10, 11, 18, 0, tzinfo=UTC))  # 11 Oct 23:30 IST
    assert r.explain("redeemed", info(), earlier, NOW, IST).reason == "Used on 11 Oct on the T7 bill (#1041)"
    with pytest.raises(r.CodeAlreadyUsed) as e:
        r.raise_for("redeemed", r.explain("redeemed", info(), counter, NOW, IST), counter)
    assert e.value.details == {"redeemed_at": counter.redeemed_at.isoformat(), "channel": "counter",
                               "redeemed_by_name": "Ravi", "order_number": None, "table_label": None}
    with pytest.raises(r.InvalidCode) as e2:
        r.raise_for("expired", r.Check("expired", "Expired on 12 Oct.", None), None)
    assert e2.value.message == "This isn't a valid code, or it has expired." and e2.value.details == {"verdict": "expired"}


def test_discount_rounds_down_to_paisa() -> None:
    assert r.discount_paise(12345, 15) == 1851
    assert r.discount_paise(124000, 20) == 24800


def test_discount_zero_without_percent() -> None:
    assert r.discount_paise(124000, None) == 0


def test_classify_query_name_last4_code_and_bad_digits() -> None:
    assert r.classify_query("  Riya ") == ("name", "Riya")
    assert r.classify_query("4321") == ("phone_last4", "4321")
    assert r.classify_query("back20-7kq") == ("code", "BACK20-7KQ")
    assert r.classify_query("BACK207KQ") == ("name", "BACK207KQ")  # dashless: a name query that also matches codes
    for bad in ("12", "123", "12345", "98765 43210", "   ", " a "):
        with pytest.raises(BadRequest):
            r.classify_query(bad)


def test_offer_message_valid_till_in_cafe_timezone() -> None:
    camp = Campaign(id="c", cafe_id="f", name="Back", segment="all_opted_in", template_name="weekly_offer",
                    offer_text="20% off", discount_percent=20, code_prefix="BACK", status="sending", scheduled_at=None,
                    valid_until=datetime(2026, 10, 12, 20, 0, tzinfo=UTC), created_at=NOW)
    assert campaigns.offer_message(camp, "Riya", "BACK-7KQ", IST).params[3] == "13 Oct"  # 01:30 IST on the 13th
    assert campaigns.offer_message(camp, "Riya", "BACK-7KQ", "UTC").params[3] == "12 Oct"


def test_normalise_code_strips_and_uppercases() -> None:
    assert r.normalise_code("  back20 -7kq \n") == "BACK20-7KQ"


def test_export_rupees_formatting_integer_only() -> None:
    assert export.rupees(124050) == "1240.50"
    assert export.rupees(5) == "0.05"
    assert export.rupees(0) == "0.00"
    assert export.rupees(None) == ""


def test_export_escapes_formula_cells() -> None:
    for text in ("=1+1", "+91 98", "-2", "@SUM(A1)", "\tx", "\rx"):
        assert export.cell(text) == "'" + text
    assert export.cell("Riya") == "Riya" and export.cell(None) == "" and export.cell(3) == "3"
    row = {"name": "=HYPERLINK(\"x\")", "deleted": False, "masked_phone": "+91 98••• ••123", "code": "BACK-7KQ",
           "status": "read", "sent_at": NOW, "delivered_at": None, "read_at": None, "replied_at": None, "orders": 1,
           "spent_paise": 54000, "redeemed_at": NOW, "redemption_channel": "counter", "bill_paise": 124000,
           "discount_paise": 24800, "redeemed_by_name": "Ravi", "opted_out_at": None, "opted_out_source": None}
    lines = export.rows_csv([row, {**row, "deleted": True, "name": None, "masked_phone": None}], IST).splitlines()
    assert lines[0].startswith("first_name,masked_phone,code,status,sent_at")
    assert lines[1].startswith("\"'=HYPERLINK(\"\"x\"\")\",'+91 98••• ••123,BACK-7KQ,read,2026-10-12T14:30:00+05:30")
    assert ",540.00," in lines[1] and ",counter,1240.00,248.00,Ravi," in lines[1]
    assert lines[2].startswith(",deleted,BACK-7KQ")
