# tria_engine/apps/billing/gateway.py
#
# Thin adapter over the official Razorpay SDK — the ONLY module in `billing`
# allowed to talk to the payment gateway. Everything above this boundary
# (services.py, views.py) works with plain dicts/Decimals and never touches
# razorpay internals or secrets.
#
# Design rules:
#   * The SDK import is lazy and optional at module-import time so Django
#     boots (and the non-gateway unit tests run) even before `pip install
#     razorpay` has run on a fresh checkout. A real gateway call fails with
#     an actionable message instead of a bare ImportError at startup.
#   * RAZORPAY_KEY_ID is safe to hand to the frontend checkout widget;
#     RAZORPAY_KEY_SECRET / RAZORPAY_WEBHOOK_SECRET never leave this file
#     (they are used only to sign/verify server-side).
#   * Signature verification uses the SDK's own documented helpers — no
#     hand-rolled HMAC against an undocumented API surface.
#   * Amounts cross this boundary in major units (INR) and are converted to
#     the gateway's minor units (paise) here and only here.

from decimal import Decimal

from django.conf import settings


class GatewayError(Exception):
    """Any gateway-level failure (network, invalid keys, SDK unavailable)."""


class GatewaySignatureError(GatewayError):
    """Raised when a payment/webhook signature does NOT verify. Callers must
    treat this as 'never trust, never activate' — nothing downstream may
    proceed on an unverified payload."""


def amount_in_paise(amount):
    """Razorpay works in paise (minor units); we store/math in INR (major).
    Decimal -> int paise, rounded to avoid float drift."""
    return int((Decimal(amount) * 100).to_integral_value())


def _sdk():
    """Import razorpay lazily so importing this module never hard-depends on
    the SDK being installed (see module docstring)."""
    try:
        import razorpay  # noqa: PLC0415
        from razorpay import errors as razorpay_errors  # noqa: PLC0415
    except ImportError:
        raise GatewayError(
            "The razorpay package is not installed. Run `pip install "
            "razorpay` (it is declared in requirements/base.txt)."
        ) from None

    def _client():
        return razorpay.Client(
            auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
        )

    return razorpay, razorpay_errors, _client


def get_key_id():
    """The publishable key id the frontend checkout widget needs. This is the
    ONLY secret-adjacent value that may be returned in an API response."""
    return settings.RAZORPAY_KEY_ID


def create_order(amount, currency="INR", receipt=None, notes=None):
    """Create a gateway order for `amount` (major units). Returns the SDK's
    order dict, which includes the `id` the frontend Checkout widget posts
    back on success (payment id + signature) and which the webhook handler
    uses to correlate events."""
    _razorpay, _errors, _client = _sdk()
    try:
        order = _client().order.create(
            data={
                "amount": amount_in_paise(amount),
                "currency": currency,
                "receipt": receipt or "",
                "notes": notes or {},
            }
        )
    except Exception as exc:  # noqa: BLE001 — normalize SDK/network errors
        raise GatewayError(f"Razorpay order creation failed: {exc}") from exc
    return order


def capture_payment(payment_id, amount, currency="INR"):
    """Capture an authorized payment for `amount` (major units). Razorpay
    payments against an Orders-API order are authorized at checkout and only
    settled when captured — this is the money-taking call, made only after
    the payment signature has verified server-side. Returns the SDK payment
    dict. If the payment was already captured (e.g. a racing webhook got
    there first) Razorpay answers with an error that the caller treats as
    'already settled', not as a failure."""
    _razorpay, _errors, _client = _sdk()
    try:
        payment = _client().payment.capture(
            payment_id, amount_in_paise(amount), {"currency": currency}
        )
    except Exception as exc:  # noqa: BLE001 — see above
        raise GatewayError(f"Razorpay payment capture failed: {exc}") from exc
    return payment


def verify_payment_signature(order_id, payment_id, signature):
    """Verify the checkout-callback signature (HMAC-SHA256 over
    `<order_id>|<payment_id>`, keyed with the API secret) using the SDK's own
    verifier. Raises GatewaySignatureError on ANY mismatch — the caller must
    not activate anything on failure."""
    _razorpay, _errors, _client = _sdk()
    try:
        # v2's Utility reads the signing secret from the attached client's
        # auth tuple (client.auth[1]), so the client MUST be constructed with
        # RAZORPAY_KEY_SECRET and passed in — it never leaves this module.
        client = _client()
        _razorpay.utility.Utility(client=client).verify_payment_signature(
            {
                "razorpay_order_id": order_id,
                "razorpay_payment_id": payment_id,
                "razorpay_signature": signature,
            }
        )
    except Exception as exc:  # noqa: BLE001
        raise GatewaySignatureError(
            "Payment signature verification failed."
        ) from exc
    return True


def verify_webhook_signature(raw_body, signature_header):
    """Verify a Razorpay webhook delivery. The signature is HMAC-SHA256 over
    the RAW request body (byte-for-byte — re-serializing the JSON would break
    it), keyed with the webhook secret. Raises GatewaySignatureError when the
    delivery is not authentic; the webhook view turns that into a 400 so a
    forged/tampered delivery is never processed.

    The SDK's verifier encodes its string inputs back to UTF-8 internally
    (bytes(body, "utf-8")), so the raw bytes are decoded first — the
    decode/encode round-trip is byte-identical for valid UTF-8 payloads."""
    _razorpay, _errors, _client = _sdk()
    try:
        body_text = raw_body.decode("utf-8") if isinstance(raw_body, bytes) else raw_body
    except UnicodeDecodeError as exc:
        raise GatewaySignatureError(
            "Webhook signature verification failed (undecodable body)."
        ) from exc
    try:
        _razorpay.utility.Utility().verify_webhook_signature(
            body_text, signature_header or "", settings.RAZORPAY_WEBHOOK_SECRET
        )
    except Exception as exc:  # noqa: BLE001
        raise GatewaySignatureError(
            "Webhook signature verification failed."
        ) from exc
    return True
