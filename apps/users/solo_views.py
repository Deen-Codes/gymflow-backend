"""
E.1 / SOLO MVP — Solo signup + onboarding endpoints.

Three endpoints:

  • POST /api/users/solo/signup/      {email, goals[], experience,
                                        equipment, days_per_week,
                                        full_name?}
        Creates a SOLO User + SoloProfile in one shot, then sends
        a magic-link email so the user can finish auth on their
        own device. Idempotent on email — a re-submission for an
        existing email refreshes the SoloProfile answers and
        re-sends the link rather than 400ing.

  • PATCH /api/users/solo/onboarding/ (token-auth)
        Updates the answers post-signup if the user wants to tweak
        them. Same field set as signup, all optional.

  • GET   /api/users/solo/me/         (token-auth)
        Returns {tier, has_ai_access, has_pro_access, goals,
        experience, equipment, days_per_week, trial_ends_at}.
        iOS reads this on launch to gate Pro / Pro AI features.

Design note: signup deliberately doesn't return an auth token. The
iOS client doesn't authenticate until the user taps the email link
— this keeps the password-less mental model intact AND prevents
account-takeover via signup spam (the real owner gets the link,
not the spammer who typed their email).
"""
import secrets
import logging

from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from .models import User, MagicLoginToken, SoloProfile

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------
# Validation helpers
# --------------------------------------------------------------------
_VALID_GOALS = {choice for choice, _ in SoloProfile.GOAL_CHOICES}
_VALID_EXPERIENCE = {choice for choice, _ in SoloProfile.EXPERIENCE_CHOICES}
_VALID_EQUIPMENT = {choice for choice, _ in SoloProfile.EQUIPMENT_CHOICES}


def _clean_goals(raw):
    """Filter to known goals, dedup, cap at 5."""
    if not isinstance(raw, list):
        return []
    cleaned = []
    seen = set()
    for g in raw:
        if not isinstance(g, str):
            continue
        v = g.strip().lower()
        if v in _VALID_GOALS and v not in seen:
            cleaned.append(v)
            seen.add(v)
        if len(cleaned) >= 5:
            break
    return cleaned


def _clean_choice(raw, valid_set):
    if not isinstance(raw, str):
        return ""
    v = raw.strip().lower()
    return v if v in valid_set else ""


def _clean_days(raw):
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return 3
    return max(1, min(7, n))


# AI-BUILD-ONBOARDING — input cleaners for the new fields.

_VALID_TRAINING_DAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}


def _clean_training_days(raw):
    """Normalise to lowercase 3-letter weekday codes, dedup, cap at 7."""
    if not isinstance(raw, list):
        return []
    cleaned = []
    seen = set()
    for d in raw:
        if not isinstance(d, str):
            continue
        v = d.strip().lower()[:3]
        if v in _VALID_TRAINING_DAYS and v not in seen:
            cleaned.append(v)
            seen.add(v)
        if len(cleaned) >= 7:
            break
    return cleaned


def _clean_session_minutes(raw):
    """Round to the nearest sane chip value. 0 = unspecified."""
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return 0
    if n <= 0:
        return 0
    # Snap to standard chip values to keep the AI's reasoning
    # simple. Anything 90+ becomes 90.
    for chip in (30, 45, 60, 75, 90):
        if n <= chip:
            return chip
    return 90


def _clean_avoidances(raw):
    """Cap each item at 80 chars and the list at 12 entries — these
    flow into the AI system prompt verbatim, so input hygiene
    matters."""
    if not isinstance(raw, list):
        return []
    cleaned = []
    seen = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        v = item.strip()[:80]
        key = v.lower()
        if v and key not in seen:
            cleaned.append(v)
            seen.add(key)
        if len(cleaned) >= 12:
            break
    return cleaned


def _username_from_email(email: str) -> str:
    """Strip the @domain off, sanitise, fall back to a random suffix
    on collision."""
    base = "".join(
        ch for ch in email.split("@", 1)[0].lower()
        if ch.isalnum() or ch == "_"
    )[:24]
    if not base:
        base = "solo"
    candidate = base
    n = 1
    while User.objects.filter(username=candidate).exists():
        n += 1
        candidate = f"{base}{n}"
        if n > 999:
            candidate = f"{base}-{secrets.token_hex(3)}"
            break
    return candidate


# --------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------
@csrf_exempt
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def solo_signup_view(request):
    """Create (or update) a Solo user + email a magic-link.

    Always returns 200 if the input is well-formed — never leaks
    "this email already exists" because that's a privacy footgun.
    """
    data = request.data or {}

    email = (data.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return Response(
            {"detail": "Enter a valid email address."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    goals       = _clean_goals(data.get("goals"))
    experience  = _clean_choice(data.get("experience"), _VALID_EXPERIENCE)
    equipment   = _clean_choice(data.get("equipment"),  _VALID_EQUIPMENT)
    days        = _clean_days(data.get("days_per_week"))
    full_name   = (data.get("full_name") or "").strip()[:120]

    # Idempotent on email — find OR create. Existing accounts:
    #
    #   • TRAINER          → never demote. Reject so a trainer doesn't
    #                        accidentally lose their dashboard.
    #   • CLIENT w/ trainer→ never demote. Reject so a paired client
    #                        keeps their PT.
    #   • CLIENT no trainer→ convert to SOLO. They're effectively a
    #                        solo user already; this just labels the
    #                        account properly so the app routes them
    #                        through the Solo experience.
    #   • SOLO             → no role change; just refresh answers.
    #   • Brand new        → create as SOLO.
    user = User.objects.filter(email__iexact=email).first()
    is_new = user is None

    if is_new:
        user = User.objects.create(
            username=_username_from_email(email),
            email=email,
            role=User.SOLO,
        )
        # Random unusable password — the user authenticates via magic
        # link, never types one. Setting an unusable password (rather
        # than `None`) lets `user.set_unusable_password()` semantics
        # stand and keeps Django's auth signals happy.
        user.set_unusable_password()
        if full_name:
            parts = full_name.split(maxsplit=1)
            user.first_name = parts[0][:30]
            if len(parts) == 2:
                user.last_name = parts[1][:30]
        user.save()
    else:
        # Decide whether to convert the existing account to SOLO.
        if user.role == User.TRAINER:
            # Don't silently turn a trainer into a solo account.
            return Response(
                {"detail": "This email belongs to a trainer account. Use the trainer login instead."},
                status=status.HTTP_409_CONFLICT,
            )
        if user.role == User.CLIENT:
            # Convert iff there's no active trainer pairing. Pairing
            # detection: ClientProfile.trainer is non-null.
            client_profile = getattr(user, "client_profile", None)
            has_trainer = client_profile is not None and client_profile.trainer_id is not None
            if has_trainer:
                # User has an active PT — refuse the auto-conversion.
                # They can manually un-pair via the Profile sheet first.
                return Response(
                    {"detail": "This email is already paired with a trainer. Unpair from your trainer first to switch to Solo."},
                    status=status.HTTP_409_CONFLICT,
                )
            # No active trainer → safe to convert. Drop the orphan
            # ClientProfile (no trainer FK to lose) so home/nutrition
            # views resolve through the SOLO branch instead of the
            # CLIENT branch.
            if client_profile is not None:
                client_profile.delete()
            user.role = User.SOLO
            user.save(update_fields=["role"])

        # Refresh names if the caller passed them — useful for SOLO
        # users who started with just an email and added their name on
        # screen 2.
        if full_name:
            parts = full_name.split(maxsplit=1)
            user.first_name = parts[0][:30]
            if len(parts) == 2:
                user.last_name = parts[1][:30]
            user.save(update_fields=["first_name", "last_name"])

    # Update / create the SoloProfile. Only refresh fields that the
    # caller actually supplied — partial signups (just email) don't
    # wipe previously stored answers.
    profile, _ = SoloProfile.objects.get_or_create(user=user)
    if goals:
        profile.goals = goals
    if experience:
        profile.experience = experience
    if equipment:
        profile.equipment = equipment
    if days:
        profile.days_per_week = days
    profile.save()

    # Re-derive macro targets if the goals changed — the user picked
    # "lose fat" vs "build muscle" and we owe them a fresh recommended
    # daily intake on the Nutrition tab. Idempotent + cheap.
    if goals:
        profile.compute_default_macro_targets(save=True)

    # Send the magic link unconditionally on signup — this is how the
    # user logs in. Reusing the same token plumbing as the regular
    # magic-link path so the deep-link / web-bridge / email template
    # all stay single-sourced.
    try:
        from .views import _send_magic_link_email, _magic_link_urls, _client_ip
        record = MagicLoginToken.objects.create(
            user=user,
            token=secrets.token_urlsafe(32),
            requested_ip=_client_ip(request),
        )
        deep_link, web_link = _magic_link_urls(record.token)
        _send_magic_link_email(user=user, deep_link=deep_link, web_link=web_link)
    except Exception:
        logger.exception("Solo signup magic-link send failed for %s", email)

    return Response(
        {
            "detail": (
                "Check your email — we sent a one-tap sign-in link. "
                "It expires in 10 minutes."
            ),
            "is_new": is_new,
        },
        status=status.HTTP_200_OK,
    )


@csrf_exempt
@api_view(["PATCH"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def solo_onboarding_update_view(request):
    """Partial update of the SoloProfile answers post-signup."""
    user = request.user
    if user.role != User.SOLO:
        return Response(
            {"detail": "Solo onboarding is only available to Solo accounts."},
            status=status.HTTP_403_FORBIDDEN,
        )

    profile, _ = SoloProfile.objects.get_or_create(user=user)
    data = request.data or {}

    if "goals" in data:
        profile.goals = _clean_goals(data["goals"])
    if "experience" in data:
        profile.experience = _clean_choice(data["experience"], _VALID_EXPERIENCE)
    if "equipment" in data:
        profile.equipment = _clean_choice(data["equipment"], _VALID_EQUIPMENT)
    if "days_per_week" in data:
        profile.days_per_week = _clean_days(data["days_per_week"])

    # AI-BUILD-ONBOARDING — three new fields captured during the
    # cinematic AI build flow. Each is independently updatable so
    # users can tweak any one from Profile later without resetting
    # the others.
    if "training_days" in data:
        profile.training_days = _clean_training_days(data["training_days"])
    if "session_minutes" in data:
        profile.session_minutes = _clean_session_minutes(data["session_minutes"])
    if "avoidances" in data:
        profile.avoidances = _clean_avoidances(data["avoidances"])

    profile.save()

    return Response(_solo_payload(profile))


@csrf_exempt
@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def solo_convert_view(request):
    """POST /api/users/solo/convert/

    Flip the calling user's account from CLIENT → SOLO. Same safety
    rails as the signup-time conversion: refused for trainers, refused
    for clients who currently have an active trainer pairing.

    Used by the iOS Profile sheet's "Solo" mode toggle so users who
    are already logged in (with a no-trainer client account from
    pre-Solo days) can switch experiences without re-signing-up.
    """
    user = request.user
    if user.role == User.TRAINER:
        return Response({"detail": "Trainer accounts can't switch to Solo."}, status=status.HTTP_403_FORBIDDEN)
    if user.role == User.SOLO:
        return Response({"ok": True, "already_solo": True})

    client_profile = getattr(user, "client_profile", None)
    if client_profile is not None and client_profile.trainer_id is not None:
        return Response(
            {"detail": "Unpair from your trainer first."},
            status=status.HTTP_409_CONFLICT,
        )
    if client_profile is not None:
        client_profile.delete()
    user.role = User.SOLO
    user.save(update_fields=["role"])
    SoloProfile.objects.get_or_create(user=user)
    return Response({"ok": True, "already_solo": False})


@csrf_exempt
@api_view(["GET"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def solo_me_view(request):
    """Return the caller's solo profile + entitlement flags. Returns
    a default-ish payload for non-solo users so iOS can call this
    unconditionally on launch without 403-handling everywhere."""
    user = request.user
    if user.role != User.SOLO:
        return Response({
            "is_solo":         False,
            "tier":            None,
            "has_ai_access":   False,
            "has_pro_access":  False,
            "goals":           [],
            "experience":      "",
            "equipment":       "",
            "days_per_week":   0,
            "trial_ends_at":   None,
        })

    profile, _ = SoloProfile.objects.get_or_create(user=user)
    # _solo_payload already includes "is_solo": True for this branch.
    return Response(_solo_payload(profile))


def _solo_payload(profile: SoloProfile) -> dict:
    """Shared serialisation shape used by `solo_me_view` +
    `solo_onboarding_update_view`. ALWAYS includes `is_solo: True`
    because this helper only runs for SOLO accounts — the non-
    solo branch in `solo_me_view` builds its own dict. iOS's
    `SoloMeResponse.isSolo` is non-optional, so the PATCH-response
    decode would barf without this key."""
    return {
        "is_solo":          True,
        "tier":             profile.tier,
        "has_ai_access":    profile.has_ai_access,
        "has_pro_access":   profile.has_pro_access,
        "goals":            profile.goals,
        "experience":       profile.experience,
        "equipment":        profile.equipment,
        "days_per_week":    profile.days_per_week,
        # AI-BUILD-ONBOARDING — surface the new fields so iOS can
        # detect "do we already have this answer?" and skip pages
        # in the AI build onboarding flow.
        "training_days":    profile.training_days,
        "session_minutes":  profile.session_minutes,
        "avoidances":       profile.avoidances,
        "trial_ends_at":    profile.trial_ends_at.isoformat() if profile.trial_ends_at else None,
    }
