import hashlib
import hmac


def verify_glpi_signature(body: bytes, timestamp: str, signature: str, secret: str) -> bool:
    """Verify the `X-GLPI-signature` header of a GLPI 11 webhook.

    GLPI signs `hmac_sha256(body + timestamp, secret)` (see `Webhook::raise` in GLPI).
    The timestamp is not bounded in time: GLPI queues webhooks and may resend them
    long after their creation, with the original headers.
    """
    expected = hmac.new(secret.encode(), body + timestamp.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
