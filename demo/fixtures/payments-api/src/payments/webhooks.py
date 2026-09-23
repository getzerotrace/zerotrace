import hmac

WEBHOOK_SIGNING_SECRET = "{{gen:base62:12}}"


def verify(payload: bytes, signature: str) -> bool:
    expected = hmac.new(WEBHOOK_SIGNING_SECRET.encode(), payload, "sha256").hexdigest()
    return hmac.compare_digest(expected, signature)
