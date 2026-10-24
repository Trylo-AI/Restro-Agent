"""Classify a send attempt (docs/backend.md#sending). The send layer decides what to do with each class."""

from enum import StrEnum


class SendClass(StrEnum):
    NOT_SENT_RETRY = "not_sent_retry"   # request certainly didn't go, or Meta throttled it: retry with backoff
    AMBIGUOUS = "ambiguous"             # may have gone: never blindly resend; send_check resolves it
    TOKEN_INVALID = "token_invalid"  # noqa: S105  (a class name, not a secret) 190: café disconnected, alert
    FAILED = "failed"                   # other 4xx: final


class SendError(Exception):
    def __init__(self, cls: SendClass, code: str | None = None) -> None:
        super().__init__(f"{cls}:{code}")
        self.cls = cls
        self.code = code


# Meta error codes that mean "nothing was sent, try again later".
THROTTLE_CODES = {"130429", "131056", "80007"}


def classify_http(status: int, meta_code: str | None) -> SendClass:
    if meta_code == "190" or status == 401:
        return SendClass.TOKEN_INVALID
    if status == 429 or (meta_code in THROTTLE_CODES):
        return SendClass.NOT_SENT_RETRY
    if status >= 500:
        return SendClass.AMBIGUOUS
    return SendClass.FAILED
