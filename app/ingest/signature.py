"""Razorpay webhook signature verification (standard scheme).

HMAC-SHA256 over the *raw* request body using the webhook secret,
hex-digest compared against the `X-Razorpay-Signature` header.
The raw bytes matter: verifying after JSON re-serialization is the
classic signature-break bug. https://razorpay.com/docs/webhooks/validate/
"""

import hashlib
import hmac


def verify_signature(raw_body: bytes, signature: str | None, secret: str) -> bool:
    if not raw_body or not signature or not secret:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())
