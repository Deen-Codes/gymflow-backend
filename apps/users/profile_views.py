"""Profile-tab API endpoints (task #30 / P.1.1).

Wires up everything the redesigned iOS Profile screen calls:

  • GET  /api/users/me/lifetime-stats/      Workouts + volume + minutes + member_since
  • GET  /api/users/me/avatar/              Returns the user's avatar (base64) or 404
  • POST /api/users/me/avatar/              Uploads a new avatar (base64 in body)
  • DELETE /api/users/me/avatar/            Clears the avatar
  • POST /api/users/me/username/            Change username (with availability check)
  • GET  /api/users/username/check/?u=foo   Live availability while typing
  • GET  /api/users/me/notification-prefs/  Per-channel toggles
  • PATCH /api/users/me/notification-prefs/ Update some/all toggles
  • POST /api/users/me/delete/              Account deletion (immediate; cascades)

Email-change isn't here — that needs a verification round-trip
which is its own feature (deferred).

In-app password change isn't here — magic-link is the primary
sign-in path, so no password to change. Existing legacy users
can still hit the web portal/password-reset/ flow if they need
to set / replace one.
"""

import base64
import logging
import re

from django.contrib.auth import logout
from django.db.models import Sum
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response

from .models import User
from .serializers import UserMeSerializer

log = logging.getLogger(__name__)


# Username constraints — kept lenient (most apps allow 3–30 chars,
# letters / digits / underscore). Reject anything obviously hostile
# (whitespace, control chars) at the validator level.
USERNAME_REGEX = re.compile(r"^[A-Za-z0-9_]{3,30}$")

# Reserved usernames you can't claim — admin paths, public pages, etc.
RESERVED_USERNAMES = {
    "admin", "root", "support", "help", "billing", "api",
    "www", "mail", "test", "gymflow", "system",
}


# ---------------------------------------------------------------------
# Lifetime stats
# ---------------------------------------------------------------------


@api_view(["GET"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def lifetime_stats_for_me(request):
    """Workouts logged + total volume + total minutes + member_since.

    Computed from the WorkoutSession + SetPerformance tables so the
    numbers match what the iOS app would compute locally — the
    server-side version is just authoritative across devices.

    Returns 0s when the user has no logged sessions yet.
    """
    from apps.workouts.models import WorkoutSession, SetPerformance

    user = request.user
    sessions = WorkoutSession.objects.filter(user=user, is_complete=True)
    workouts_count = sessions.count()
    total_seconds = sessions.aggregate(total=Sum("duration"))["total"] or 0
    total_minutes = total_seconds // 60

    # Volume — sum of (weight × reps) across every set on every
    # session. weight + reps are CharField (legacy), so we coerce
    # row-by-row in Python; the dataset is small enough that a
    # full scan is fine. Once the column types tighten we can do
    # this in pure SQL.
    total_volume_kg = 0.0
    set_rows = SetPerformance.objects.filter(
        exercise_session__workout_session__user=user,
        exercise_session__workout_session__is_complete=True,
    ).values_list("weight", "reps")
    for weight_str, reps_str in set_rows:
        try:
            w = float(weight_str or 0)
            r = float(reps_str or 0)
            total_volume_kg += w * r
        except (TypeError, ValueError):
            continue

    return Response({
        "workouts_completed": workouts_count,
        "total_volume_kg":    round(total_volume_kg, 1),
        "total_minutes":      total_minutes,
        "member_since":       user.date_joined.isoformat() if user.date_joined else None,
    })


# ---------------------------------------------------------------------
# Avatar (base64-on-row storage)
# ---------------------------------------------------------------------


# 1.4 MB is the upper bound on the b64 string length we accept,
# corresponding to ~1 MB raw image after decode. iOS pre-downsizes
# before upload — this is just the server-side safety net.
AVATAR_MAX_LEN = 1_400_000


@api_view(["GET", "POST", "DELETE"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def avatar_for_me(request):
    user = request.user

    if request.method == "GET":
        if not user.avatar_base64:
            return Response({"detail": "No avatar set."}, status=status.HTTP_404_NOT_FOUND)
        return Response({"avatar_base64": user.avatar_base64})

    if request.method == "DELETE":
        user.avatar_base64 = None
        user.save(update_fields=["avatar_base64"])
        return Response({"detail": "Avatar removed."})

    # POST — set/replace.
    raw = (request.data.get("avatar_base64") or "").strip()
    if not raw:
        return Response(
            {"detail": "Missing avatar_base64."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    # Strip a `data:image/...;base64,` prefix if iOS sends one;
    # we only persist the raw payload.
    if raw.startswith("data:") and ";base64," in raw:
        raw = raw.split(";base64,", 1)[1]
    if len(raw) > AVATAR_MAX_LEN:
        return Response(
            {"detail": "Image too large. Pick something under 1 MB."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    # Validate that it actually decodes (rejects garbage that
    # would crash any future image-processing pipeline). We don't
    # store the decoded bytes — just the b64 string.
    try:
        base64.b64decode(raw, validate=True)
    except Exception:
        return Response(
            {"detail": "Image data didn't decode."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user.avatar_base64 = raw
    user.save(update_fields=["avatar_base64"])
    return Response({"detail": "Avatar saved."})


# ---------------------------------------------------------------------
# Username change + availability
# ---------------------------------------------------------------------


def _is_valid_username(username):
    if not USERNAME_REGEX.match(username):
        return False, "Usernames are 3–30 characters, letters / digits / underscore only."
    if username.lower() in RESERVED_USERNAMES:
        return False, "That username is reserved."
    return True, None


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def username_check_view(request):
    """Live availability — used by the iOS Profile UI as the user
    types. Returns `available: bool`.
    """
    username = (request.query_params.get("u") or "").strip()
    valid, reason = _is_valid_username(username)
    if not valid:
        return Response({"available": False, "detail": reason})
    taken = User.objects.filter(username__iexact=username).exists()
    return Response({"available": not taken})


@csrf_exempt
@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def change_username_view(request):
    """Change the authenticated user's username. Returns 409 on
    conflict so iOS can show "that's taken" inline.
    """
    user = request.user
    new_username = (request.data.get("username") or "").strip()
    valid, reason = _is_valid_username(new_username)
    if not valid:
        return Response(
            {"detail": reason},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if User.objects.filter(username__iexact=new_username).exclude(pk=user.pk).exists():
        return Response(
            {"detail": "That username is already taken."},
            status=status.HTTP_409_CONFLICT,
        )
    user.username = new_username
    user.save(update_fields=["username"])
    return Response(UserMeSerializer(user).data)


# ---------------------------------------------------------------------
# Notification preferences
# ---------------------------------------------------------------------

DEFAULT_NOTIFICATION_PREFS = {
    "push_enabled":           True,
    "workout_reminders":      True,
    "check_in_nudges":        True,
    "coach_messages":         True,
    "marketing":              False,
    "quiet_hours_enabled":    False,
    "quiet_hours_start_min":  22 * 60,
    "quiet_hours_end_min":    7 * 60,
}


def _resolved_notification_prefs(user):
    """Merge DB-stored prefs with defaults so iOS always sees a
    full payload regardless of what's been saved."""
    stored = user.notification_prefs or {}
    return {**DEFAULT_NOTIFICATION_PREFS, **stored}


@api_view(["GET", "PATCH"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def notification_prefs_for_me(request):
    user = request.user
    if request.method == "GET":
        return Response(_resolved_notification_prefs(user))

    # PATCH — partial update. iOS sends only the keys it changed.
    incoming = request.data or {}
    if not isinstance(incoming, dict):
        return Response(
            {"detail": "Body must be a JSON object."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    # Whitelist — never accept arbitrary keys.
    allowed_keys = set(DEFAULT_NOTIFICATION_PREFS.keys())
    cleaned = {k: v for k, v in incoming.items() if k in allowed_keys}
    merged = {**(user.notification_prefs or {}), **cleaned}
    user.notification_prefs = merged
    user.save(update_fields=["notification_prefs"])
    return Response(_resolved_notification_prefs(user))


# ---------------------------------------------------------------------
# Account deletion
# ---------------------------------------------------------------------


@csrf_exempt
@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def delete_account_view(request):
    """Immediate account deletion. Cascades through related rows
    via the existing `on_delete=CASCADE` foreign keys (workouts,
    nutrition, progress, trophies all hang off User).

    iOS confirm sub-sheet already gates this with a "double tap to
    destroy" pattern, so by the time we get here the user has
    explicitly confirmed. We log them out before deleting so any
    stale session state is cleared.
    """
    user = request.user
    user_id = user.id
    username = user.username

    # Drop the auth token first so a stolen token can't be reused
    # to hit some other endpoint mid-deletion.
    try:
        user.auth_token.delete()
    except Exception:
        pass
    logout(request)

    user.delete()
    log.info("Deleted account %s (id=%s)", username, user_id)
    return Response({"detail": "Account deleted."})
