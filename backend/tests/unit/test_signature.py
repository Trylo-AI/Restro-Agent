from app.core.signature import sign, verify


def test_valid_signature() -> None:
    body = b'{"entry":[]}'
    assert verify(body, sign(body, "secret"), "secret")


def test_tampered_body_or_wrong_secret_or_missing_header() -> None:
    body = b'{"entry":[]}'
    header = sign(body, "secret")
    assert not verify(body + b" ", header, "secret")
    assert not verify(body, header, "other")
    assert not verify(body, None, "secret")
    assert not verify(body, header.removeprefix("sha256="), "secret")
