"""
Debug endpoints — flip subscription tier + reset AI usage state
without touching Stripe / IAP.

The Profile screen has a DEBUG-only panel (Free / Pro / Pro AI buttons
+ "Reset account state + sign out") that previously only patched
the iOS-side `soloEntitlements` in memory. The backend was left
untouched, so:

  • Tapping "Pro AI" let iOS pretend the user had AI access, but
    when AI build/chat/describe actually called the backend, the
    server-side `SoloProfile.tier` was still "free", so the
    request 402'd into the paywall. Useless for testing.

  • Tapping "Reset account state + sign out" wiped UserDefaults
    locally, but `solo_ai_build_preview_used` lived on the server
    in `User.notification_prefs` and survived the wipe. So after a
    "fresh" sign-in the AI build view 402'd because the server
    still remembered the preview was used.

These endpoints fix both. They flip the actual `SoloProfile.tier`
and (optionally) wipe the AI-usage keys from `notification_prefs`,
making the debug menu round-trip its state to the backend.

GUARDRAILS:
  • Only accessible when `settings.DEBUG=True` OR the
    `ENABLE_DEBUG_RESET=1` env var is set. Render production
    deploys neither, so end users can never hit this in App Store
    builds.
  • Each user can only reset / re-tier THEIR OWN row. No cross-
    user mutation possible.
  • Wraps changes in `transaction.atomic()` so a half-applied
    state never lands on disk.

Endpoints:
  POST /api/users/_debug/set-state/
    Body: {"tier": "free"|"pro"|"pro_ai"|null, "reset_caches": bool}
    Returns: {"ok": true, "tier": "...", "reset": bool}
"""
import logging
import os

from django.conf import settings
from django.db import transaction
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import (
    api_view, authentication_classes, permission_classes,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import User, SoloProfile

log = logging.getLogger(__name__)


def _debug_enabled() -> bool:
    """Both knobs gate the debug endpoints — production has neither."""
    return bool(settings.DEBUG) or os.environ.get("ENABLE_DEBUG_RESET") == "1"


@csrf_exempt
@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def solo_debug_set_state(request):
    if not _debug_enabled():
        return Response(
            {"detail": "Debug endpoints disabled in production."},
            status=status.HTTP_403_FORBIDDEN,
        )

    user = request.user
    if user.role != User.SOLO:
        return Response(
            {"detail": "Solo accounts only."},
            status=status.HTTP_403_FORBIDDEN,
        )

    data = request.data or {}
    tier = data.get("tier")
    reset_caches = bool(data.get("reset_caches"))

    valid_tiers = {SoloProfile.TIER_FREE, SoloProfile.TIER_PRO, SoloProfile.TIER_PRO_AI}
    if tier is not None and tier not in valid_tiers:
        return Response(
            {"detail": f"Invalid tier. Use one of: {sorted(valid_tiers)}"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    with transaction.atomic():
        profile, _ = SoloProfile.objects.get_or_create(user=user)

        if tier is not None:
            profile.tier = tier
            # Trial bookkeeping — when flipping to Pro AI we also
            # set a future trial end so any iOS surface that reads
            # trial-active state behaves correctly. When flipping
            # back to free, clear the trial entirely.
            if tier == SoloProfile.TIER_PRO_AI:
                # Set 14-day trial window starting now (mirrors the
                # real IAP flow). Skip if trial already started so
                # repeated taps don't refresh the clock.
                if not profile.trial_started_at:
                    from django.utils import timezone
                    from datetime import timedelta
                    now = timezone.now()
                    profile.trial_started_at = now
                    profile.trial_ends_at = now + timedelta(days=14)
            elif tier == SoloProfile.TIER_FREE:
                profile.trial_started_at = None
                profile.trial_ends_at = None
                profile.tier_active_until = None
            profile.save(update_fields=[
                "tier", "trial_started_at", "trial_ends_at", "tier_active_until",
            ])

        if reset_caches:
            # Wipe the AI-related keys from notification_prefs so
            # the next AI build / chat / describe call behaves like
            # a fresh account.
            prefs = user.notification_prefs or {}
            for key in ("solo_ai_build_preview_used", "ai_usage", "recent_feedback"):
                prefs.pop(key, None)
            user.notification_prefs = prefs
            user.save(update_fields=["notification_prefs"])

    log.info(
        "debug set-state: user_id=%s tier=%s reset_caches=%s",
        user.id, tier, reset_caches,
    )
    return Response({
        "ok":           True,
        "tier":         profile.tier,
        "has_ai_access": profile.has_ai_access,
        "has_pro_access": profile.has_pro_access,
        "reset":        reset_caches,
    })
