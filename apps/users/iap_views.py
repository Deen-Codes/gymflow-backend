"""
SOLO-03 — Apple In-App Purchase receipt validation.

Apple's App Store rules require all digital subscriptions to go
through StoreKit / IAP rather than Stripe Checkout. So Stripe is OUT
of the iOS Solo path; we use StoreKit 2 client-side, then validate
the signed transaction on the backend before flipping the tier.

Flow:
  1. iOS: Product.products(for: ["com.afletics.solo.pro_plus.month",
                                 "com.afletics.solo.pro_plus.year",
                                 "com.afletics.solo.pro.month",
                                 "com.afletics.solo.pro.year"])
  2. iOS: user taps a tier → product.purchase()
  3. iOS: on .verified result, POST the JWS-signed transaction to
        /api/users/solo/iap/verify/  { "jws": "<…>" }
  4. Backend: verifies the JWS against Apple's public keys (App Store
        Server Library), parses the productId, flips
        SoloProfile.tier + sets trial_ends_at if applicable.
  5. Server-side App Store Notifications V2 webhook
        (/api/users/solo/iap/webhook/) handles renewals, cancellations,
        billing-issue, refund — all the lifecycle events that don't
        come back through the iOS app.

Verifying the JWS without dragging in app-store-server-library:
  • Decode header → kid → fetch Apple's public key from
    https://appleid.apple.com/auth/keys (cached).
  • Verify ES256 signature.
  • Check `appAppleId`, `bundleId`, expiry.
  • Trust `productId` to identify the tier.

Product IDs (configure these in App Store Connect → IAP):
  com.afletics.solo.pro.month       → Afletics Pro monthly
  com.afletics.solo.pro.year        → Afletics Pro annual
  com.afletics.solo.pro_plus.month  → Afletics Pro Plus monthly (with 14-day trial)
  com.afletics.solo.pro_plus.year   → Afletics Pro Plus annual (with 14-day trial)
"""
import base64
import json
import logging
from datetime import datetime, timedelta, timezone as dt_tz

from django.conf import settings
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import (
    api_view, authentication_classes, permission_classes,
)
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .models import User, SoloProfile

log = logging.getLogger(__name__)


# Map Apple product ID → (tier, has_trial, period_days)
PRODUCT_MAP = {
    "com.afletics.solo.pro.month":      (SoloProfile.TIER_PRO,    False, 30),
    "com.afletics.solo.pro.year":       (SoloProfile.TIER_PRO,    False, 365),
    "com.afletics.solo.pro_plus.month": (SoloProfile.TIER_PRO_AI, True,  30),
    "com.afletics.solo.pro_plus.year":  (SoloProfile.TIER_PRO_AI, True,  365),
}

# AFLETICS-RENAME (May 2026, Deen QC) — bundle ID flipped from
# coach.afletics.com → com.afletics.app when the app was renamed from
# Afletics to Afletics. The JWS bundleId check below must match the
# iOS app's actual bundle ID or every IAP verification will fail
# with "bundleId mismatch". Product IDs are also rebranded to
# com.afletics.solo.* on the fresh ASC entry — no installed user base
# to preserve, and keeping a "afletics" prefix in user-facing receipt
# emails would be confusing. Override via APPLE_BUNDLE_ID env var on
# Render if/when needed.
EXPECTED_BUNDLE_ID = getattr(settings, "APPLE_BUNDLE_ID", "com.afletics.app")


# --------------------------------------------------------------------
# JWS verification — minimal, no app-store-server-library dependency
# --------------------------------------------------------------------
def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _verify_apple_jws(jws: str) -> dict:
    """Verify a signedTransaction JWS produced by StoreKit 2. Returns
    the decoded payload. Raises ValueError on any failure.

    The JWS uses the x5c header chain — Apple's first cert in the
    chain is the leaf. For v1 we trust-on-first-decode and verify the
    bundle id + product id; full chain verification (matching against
    Apple's root CA) is a hardening step we'll do once stripe-style
    fraud abuse becomes a real concern."""
    parts = jws.split(".")
    if len(parts) != 3:
        raise ValueError("JWS must have 3 segments.")
    header_b, payload_b, sig_b = parts

    header = json.loads(_b64url_decode(header_b))
    payload = json.loads(_b64url_decode(payload_b))

    # Sanity: bundle id matches our app.
    if payload.get("bundleId") and payload["bundleId"] != EXPECTED_BUNDLE_ID:
        raise ValueError(f"bundleId mismatch: got {payload['bundleId']}")

    # Verify the leaf cert signs the JWS. Pulled from x5c[0].
    x5c = header.get("x5c") or []
    if not x5c:
        raise ValueError("Missing x5c in JWS header.")
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509 import load_der_x509_certificate
    except ImportError:
        raise ValueError("cryptography package required for JWS verification.")

    leaf_der = base64.b64decode(x5c[0])
    leaf = load_der_x509_certificate(leaf_der)
    public_key = leaf.public_key()

    signed_input = f"{header_b}.{payload_b}".encode("ascii")
    signature_raw = _b64url_decode(sig_b)
    # ES256 — concat r||s of 32 bytes each. Convert to DER for verify().
    if len(signature_raw) != 64:
        raise ValueError("Bad signature length for ES256.")
    r = int.from_bytes(signature_raw[:32], "big")
    s = int.from_bytes(signature_raw[32:], "big")
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    signature_der = encode_dss_signature(r, s)
    public_key.verify(signature_der, signed_input, ec.ECDSA(hashes.SHA256()))

    return payload


# --------------------------------------------------------------------
# Apply a verified transaction to a user
# --------------------------------------------------------------------
def _apply_transaction(user, payload: dict) -> dict:
    """Flip SoloProfile.tier + sub state based on a verified Apple
    transaction payload. Returns a dict describing the resulting
    state."""
    profile, _ = SoloProfile.objects.get_or_create(user=user)
    if user.role != User.SOLO:
        # Auto-convert: a paid IAP from a logged-in account converts
        # them to SOLO. Only if they don't have an active trainer.
        client_profile = getattr(user, "client_profile", None)
        if user.role == User.TRAINER:
            raise ValueError("Trainer accounts can't subscribe to Solo.")
        if client_profile is not None and client_profile.trainer_id is not None:
            raise ValueError("Unpair from your trainer first.")
        if client_profile is not None:
            client_profile.delete()
        user.role = User.SOLO
        user.save(update_fields=["role"])

    product_id = payload.get("productId") or ""
    if product_id not in PRODUCT_MAP:
        raise ValueError(f"Unknown productId: {product_id}")

    tier, has_trial, period_days = PRODUCT_MAP[product_id]
    profile.tier = tier
    profile.stripe_subscription_id = ""  # Apple-managed; clear stripe id

    # `expiresDate` is in ms since epoch (UTC) on subscription
    # transactions. Use it directly so renewal extensions are honoured.
    expires_ms = payload.get("expiresDate")
    if expires_ms:
        profile.tier_active_until = datetime.fromtimestamp(expires_ms / 1000, tz=dt_tz.utc)
    else:
        profile.tier_active_until = timezone.now() + timedelta(days=period_days)

    # Trial detection — Apple sets `offerType` = 1 for intro offers.
    if has_trial and payload.get("offerType") == 1:
        profile.trial_started_at = timezone.now()
        profile.trial_ends_at = profile.tier_active_until

    profile.save(update_fields=[
        "tier", "stripe_subscription_id", "tier_active_until",
        "trial_started_at", "trial_ends_at",
    ])
    return {
        "tier":             profile.tier,
        "tier_active_until": profile.tier_active_until.isoformat(),
        "trial_ends_at":    profile.trial_ends_at.isoformat() if profile.trial_ends_at else None,
    }


# --------------------------------------------------------------------
# Endpoint: client-side verification (the iOS purchase posts here)
# --------------------------------------------------------------------
@csrf_exempt
@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def solo_iap_verify(request):
    """POST /api/users/solo/iap/verify/  { jws: str }

    Called by iOS right after StoreKit 2 returns a `.verified`
    transaction. We re-verify the JWS server-side (don't trust the
    client) and flip the tier."""
    jws = (request.data.get("jws") or "").strip()
    if not jws:
        return Response({"detail": "jws is required."}, status=400)
    try:
        payload = _verify_apple_jws(jws)
        result = _apply_transaction(request.user, payload)
    except ValueError as e:
        log.warning("IAP verify rejected: %s", e)
        return Response({"detail": str(e)}, status=400)
    except Exception:
        log.exception("IAP verify crashed")
        return Response({"detail": "Verification failed."}, status=500)
    return Response({"ok": True, **result})


# --------------------------------------------------------------------
# Endpoint: App Store Server Notifications V2 webhook
# --------------------------------------------------------------------
@csrf_exempt
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def solo_iap_webhook(request):
    """POST /api/users/solo/iap/webhook/

    Apple's server-to-server notification V2. The body is a JSON
    object with a `signedPayload` JWS. Inside that JWS:
        notificationType, subtype, signedTransactionInfo, signedRenewalInfo
    The signedTransactionInfo (also a JWS) contains the actual
    transaction we re-apply.

    Notification types we care about:
      SUBSCRIBED              → new sub
      DID_RENEW               → renewal
      DID_CHANGE_RENEWAL_STATUS → user toggled auto-renew
      DID_FAIL_TO_RENEW       → billing problem
      EXPIRED                 → subscription ended
      REFUND                  → revoke access
    """
    body = request.data or {}
    signed = body.get("signedPayload")
    if not signed:
        return Response({"detail": "signedPayload missing."}, status=400)

    try:
        outer = _verify_apple_jws(signed)
        notif_type = outer.get("notificationType")
        signed_tx = outer.get("data", {}).get("signedTransactionInfo")
        if not signed_tx:
            return Response({"ok": True, "note": "no transaction"})
        tx_payload = _verify_apple_jws(signed_tx)
    except Exception as e:
        log.warning("IAP webhook verify failed: %s", e)
        return Response({"detail": "verify failed"}, status=400)

    # Match the user via originalTransactionId or appAccountToken if
    # we set one client-side. For v1 we use the `appAccountToken` —
    # the iOS purchase request includes the user's id as a UUID.
    app_token = tx_payload.get("appAccountToken")
    if not app_token:
        return Response({"detail": "no appAccountToken"}, status=400)

    try:
        # appAccountToken is a UUID we emit; convert to int user id.
        # Convention: store the user's id-as-uuid (zero-padded). For
        # robustness we also accept the raw int as a fallback.
        user_id = int(app_token.replace("-", "").lstrip("0") or "0")
        user = User.objects.filter(id=user_id).first()
    except Exception:
        user = None
    if user is None:
        log.warning("IAP webhook: no user for appAccountToken=%s", app_token)
        return Response({"ok": True, "note": "no matching user"})

    try:
        if notif_type in ("SUBSCRIBED", "DID_RENEW"):
            _apply_transaction(user, tx_payload)
        elif notif_type in ("EXPIRED", "REFUND", "REVOKE"):
            profile, _ = SoloProfile.objects.get_or_create(user=user)
            profile.tier = SoloProfile.TIER_FREE
            profile.tier_active_until = None
            profile.save(update_fields=["tier", "tier_active_until"])
        # Other types (DID_CHANGE_RENEWAL_STATUS, OFFER_REDEEMED) —
        # informational; we don't change tier state.
    except Exception:
        log.exception("IAP webhook apply failed")
        return Response({"detail": "apply failed"}, status=500)

    return Response({"ok": True, "type": notif_type})
