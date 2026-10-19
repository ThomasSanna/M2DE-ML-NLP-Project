import hashlib
import hmac


def verify_glpi_signature(body: bytes, timestamp: str, signature: str, secret: str) -> bool:
    """Vérifie l'en-tête `X-GLPI-signature` d'un webhook GLPI 11.

    GLPI signe `hmac_sha256(body + timestamp, secret)` (cf. `Webhook::raise` dans GLPI).
    Le timestamp n'est pas borné dans le temps : GLPI met les webhooks en file et
    peut les renvoyer bien après leur création, avec les en-têtes d'origine.
    """
    expected = hmac.new(secret.encode(), body + timestamp.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
