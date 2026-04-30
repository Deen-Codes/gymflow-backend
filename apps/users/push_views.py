"""
R3-9 — APNs push registration (v0).

Stores device tokens against User.notification_prefs (existing
JSONField, no migration needed) and exposes endpoints to register
+ deregister. The actual SEND pipeline (workout reminders, AI
replies, check-in nudges) lives in `apps.users.push_send` and
runs as a celery beat / management-command task.

For v0 we only do the round-trip plumbing: iOS registers the
device token, the backend stores it, an admin / cron job can
fan out a notification by reading `notification_prefs.apns_tokens`
across all users.

A proper push delivery service (PyAPNs2 / hyper) lands in v0.5
when we wire the actual send fan-out.
"""
import logging

from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import (
    api_view, authentication_classes, permission_classes,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

log = logging.getLogger(__name__)


APNS_TOKENS_KEY = "apns_tokens"


@csrf_exempt
@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def register_apns_token(request):
    """Body: {"token": str, "environment": "production"|"sandbox"}

    Stores the device token on the user's `notification_prefs`.
    Tokens are kept in a list so a user with multiple devices
    (iPhone + iPad) gets push to all of them. Re-registering the
    same token is idempotent.

    Tokens auto-expire after 30 days of inactivity (the send
    pipeline trims feedback-failed tokens at delivery time).
    """
    token = (request.data or {}).get("token") or ""
    environment = (request.data or {}).get("environment") or "production"
    if not token or not isinstance(token, str) or len(token) < 32:
        return Response({"detail": "Invalid token."}, status=400)
    if environment not in ("production", "sandbox"):
        environment = "production"

    user = request.user
    prefs = user.notification_prefs or {}
    tokens = prefs.get(APNS_TOKENS_KEY) or []

    # Drop any prior entry for the same raw token (could be a
    # registration after env-switch from sandbox → production).
    tokens = [t for t in tokens if t.get("token") != token]
    tokens.append({
        "token":         token,
        "environment":   environment,
        "registered_at": timezone.now().isoformat(),
    })
    # Cap total tokens per user at 6 (paranoid; a normal user has
    # 1-2). Trim oldest-first.
    if len(tokens) > 6:
        tokens = tokens[-6:]

    prefs[APNS_TOKENS_KEY] = tokens
    user.notification_prefs = prefs
    user.save(update_fields=["notification_prefs"])

    return Response({"ok": True, "tokens_on_file": len(tokens)})


@csrf_exempt
@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def deregister_apns_token(request):
    """Body: {"token": str}

    Removes a single token (e.g. user disabled push in iOS
    settings, app explicitly clears it on logout).
    """
    token = (request.data or {}).get("token") or ""
    if not token:
        return Response({"detail": "token required."}, status=400)
    user = request.user
    prefs = user.notification_prefs or {}
    tokens = prefs.get(APNS_TOKENS_KEY) or []
    tokens = [t for t in tokens if t.get("token") != token]
    prefs[APNS_TOKENS_KEY] = tokens
    user.notification_prefs = prefs
    user.save(update_fields=["notification_prefs"])
    return Response({"ok": True, "tokens_on_file": len(tokens)})
