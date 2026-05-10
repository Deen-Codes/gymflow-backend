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


# ---------------------------------------------------------------------
# Setup progress — drives the in-app setup strip
# ---------------------------------------------------------------------


# Stable step IDs the iOS strip uses to address individual flags via
# PATCH. Keep these in sync with `SoloProfile.setup_*_done` fields.
SETUP_STEP_IDS = [
    ("apple_health",    "Sync Apple Health",  "Auto-fills your stats and syncs workouts."),
    ("body_stats",      "Body stats",         "Height, weight, age, sex."),
    ("goal",            "Your goal",          "Lose, maintain, or gain."),
    ("training",        "Training style",     "Experience and days per week."),
    ("nutrition_style", "Nutrition style",    "Dietary pattern and allergies."),
]

# Map step_id → SoloProfile field name. One source of truth.
_STEP_TO_FIELD = {
    "apple_health":    "setup_apple_health_done",
    "body_stats":      "setup_body_stats_done",
    "goal":            "setup_goal_done",
    "training":        "setup_training_done",
    "nutrition_style": "setup_nutrition_style_done",
}


def _setup_progress_payload(profile, *, trophy_awarded_now: bool = False) -> dict:
    """Build the wire shape consumed by the iOS SetupProgressStrip."""
    steps = []
    done_count = 0
    for step_id, label, hint in SETUP_STEP_IDS:
        done = bool(getattr(profile, _STEP_TO_FIELD[step_id]))
        if done:
            done_count += 1
        steps.append({
            "id":    step_id,
            "label": label,
            "hint":  hint,
            "done":  done,
        })
    return {
        "steps":           steps,
        "completed_count": done_count,
        "total":           len(steps),
        "complete":        done_count == len(steps),
        "trophy_awarded":  trophy_awarded_now,
    }


@csrf_exempt
@api_view(["GET", "PATCH"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def setup_progress_view(request):
    """ONBOARDING-QUICK-START — drives the Home setup strip.

    GET — return the user's per-step done flags.
    PATCH — flip one or many done flags. Body shapes:
        {"step_id": "goal", "done": true}
        {"updates": {"goal": true, "training": true}}

    Non-solo users get an empty steps list (the strip is solo-only;
    PT-managed clients have a different onboarding path).

    Awards the `set_up_strong` trophy when all 5 flip true. The
    response includes `trophy_awarded: true` on the request that
    unlocked it, so iOS can fire the unlock-toast immediately.
    """
    if request.user.role != User.SOLO:
        return Response({
            "steps": [], "completed_count": 0, "total": 0,
            "complete": True, "trophy_awarded": False,
        })

    # SoloProfile is auto-created on first access via the related
    # manager — same pattern as the rest of the solo namespace.
    try:
        profile = request.user.solo_profile
    except Exception:
        from .models import SoloProfile
        profile, _ = SoloProfile.objects.get_or_create(user=request.user)

    if request.method == "GET":
        return Response(_setup_progress_payload(profile))

    # PATCH
    data = request.data or {}
    updates = data.get("updates")
    if updates is None and "step_id" in data:
        updates = {data["step_id"]: bool(data.get("done", True))}
    if not isinstance(updates, dict):
        return Response(
            {"detail": "Body must include either {step_id, done} or {updates: {...}}."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    was_complete = profile.setup_complete

    changed_fields = []
    for step_id, raw_done in updates.items():
        field = _STEP_TO_FIELD.get(step_id)
        if field is None:
            continue  # Unknown step ID — ignore, don't 400 on noise.
        setattr(profile, field, bool(raw_done))
        changed_fields.append(field)

    # NUTRITION-QUICK-START — optional `step_data` dict applies the
    # underlying SoloProfile fields the step captured, so iOS only
    # needs one round-trip per step (vs separate /profile-update/ +
    # setup-progress calls). Quietly ignores fields not in the
    # allow-list so callers can't smuggle arbitrary writes.
    step_data = data.get("step_data") or {}
    if isinstance(step_data, dict):
        for key, raw in step_data.items():
            if key == "bodyweight_kg":
                try:
                    profile.bodyweight_kg = float(raw)
                    changed_fields.append("bodyweight_kg")
                except (TypeError, ValueError):
                    pass
            elif key == "height_cm":
                try:
                    profile.height_cm = int(float(raw))
                    changed_fields.append("height_cm")
                except (TypeError, ValueError):
                    pass
            elif key == "gender":
                if isinstance(raw, str) and raw:
                    profile.gender = raw[:16]
                    changed_fields.append("gender")
            elif key == "goals":
                if isinstance(raw, list):
                    profile.goals = [str(x) for x in raw][:8]
                    changed_fields.append("goals")
            elif key == "experience":
                if isinstance(raw, str):
                    profile.experience = raw[:20]
                    changed_fields.append("experience")
            elif key == "days_per_week":
                try:
                    val = int(raw)
                    if 1 <= val <= 7:
                        profile.days_per_week = val
                        changed_fields.append("days_per_week")
                except (TypeError, ValueError):
                    pass
            elif key == "dietary_pattern":
                if isinstance(raw, str):
                    profile.dietary_pattern = raw[:32]
                    changed_fields.append("dietary_pattern")
            elif key == "allergies":
                # Store free-text in notification_prefs JSON
                # alongside personal_details — we don't have a
                # dedicated SoloProfile column yet.
                if isinstance(raw, str):
                    prefs = request.user.notification_prefs or {}
                    pd = dict(prefs.get("personal_details") or {})
                    pd["allergies"] = raw[:500]
                    prefs["personal_details"] = pd
                    request.user.notification_prefs = prefs
                    request.user.save(update_fields=["notification_prefs"])
            elif key == "age_years":
                # Convert age → synthetic DOB (Jan 1 of birth year)
                # so we land in the existing `User.date_of_birth`
                # column. Year-precision is plenty for the macro
                # engine; we'll add full-DOB capture later when
                # the Apple Health step pulls the real one.
                try:
                    age = int(raw)
                    if 12 <= age <= 100:
                        from django.utils import timezone
                        from datetime import date
                        today = timezone.localdate()
                        request.user.date_of_birth = date(today.year - age, 1, 1)
                        request.user.save(update_fields=["date_of_birth"])
                except (TypeError, ValueError):
                    pass
            elif key == "date_of_birth":
                # Apple Health path — we get the real DOB.
                from datetime import date
                try:
                    if isinstance(raw, str) and len(raw) >= 10:
                        y, m, d = raw[:10].split("-")
                        request.user.date_of_birth = date(int(y), int(m), int(d))
                        request.user.save(update_fields=["date_of_birth"])
                except (ValueError, AttributeError):
                    pass

    if changed_fields:
        # Dedupe so save() doesn't repeat columns when both
        # done-flag and step-data write the same field.
        profile.save(update_fields=list(set(changed_fields)))

    # Award the trophy when the user crosses 4→5 for the first time.
    # `evaluate_and_award` is idempotent so re-running for an already-
    # awarded user is safe — it just returns an empty list.
    trophy_awarded_now = False
    if not was_complete and profile.setup_complete:
        try:
            from apps.trophies.services import evaluate_and_award
            newly = evaluate_and_award(request.user)
            trophy_awarded_now = any(t.code == "set_up_strong" for t in newly)
        except Exception:
            log.exception("set_up_strong trophy award failed")

    return Response(_setup_progress_payload(
        profile, trophy_awarded_now=trophy_awarded_now,
    ))
