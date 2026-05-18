import logging
import secrets

from django.conf import settings
from django.contrib.auth import login, logout
from django.core.mail import EmailMultiAlternatives
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework.authtoken.models import Token
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework import status

from .models import User, MagicLoginToken
from .serializers import (
    LoginSerializer,
    UserMeSerializer,
    ClientCreateSerializer,
    ClientListSerializer,
    AssignWorkoutPlanSerializer,
)

log = logging.getLogger(__name__)


# -------------------------------------------------------------------
# Phase 0 — Token authentication
#
# The iOS client needs an auth mechanism that survives app re-installs
# and process restarts more reliably than HTTPCookieStorage. The login
# view now issues a DRF auth token alongside the existing session, so:
#   * The Django dashboard keeps working unchanged (session cookie).
#   * The iOS client stores `token` in the Keychain and sends it as
#     `Authorization: Token <key>` on every request.
# -------------------------------------------------------------------
# `csrf_exempt` belt-and-suspenders alongside `@api_view`. Mobile clients
# don't have a CSRF cookie/token, and Django 4.x's tightened CSRF check
# can reject before DRF's csrf_exempt flag is honored. We also drop
# SessionAuthentication for this single endpoint — the iOS client never
# sends a session cookie, so DRF's enforce_csrf path is moot here.
@csrf_exempt
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def login_view(request):
    serializer = LoginSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    user = serializer.validated_data["user"]
    login(request, user)

    token, _ = Token.objects.get_or_create(user=user)

    return Response(
        {
            "message": "Login successful.",
            "token": token.key,
            "user": UserMeSerializer(user).data,
        },
        status=status.HTTP_200_OK,
    )


@csrf_exempt
@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def logout_view(request):
    # Destroy the user's auth token so a stolen token can't be reused
    # after sign-out. Wrapped in try/except because the user may have
    # signed in via session only (no token yet).
    try:
        request.user.auth_token.delete()
    except (AttributeError, Token.DoesNotExist):
        # Expected for session-only auth — fine to ignore.
        pass
    except Exception:
        # Any other exception during token cleanup we want to know
        # about — silently swallowing previously meant a broken
        # logout could persist tokens. log.exception captures the
        # full traceback to Render.
        log.exception("logout token cleanup failed unexpectedly")

    logout(request)
    return Response({"message": "Logout successful."}, status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def me_view(request):
    # PERF-ME-SELECT-RELATED (May 2026, Deen QC) — UserMeSerializer's
    # SerializerMethodFields read `trainer_profile.slug`,
    # `client_profile.trainer.business_name`,
    # `solo_profile.assigned_workout_plan.name`, etc. With a bare
    # `request.user` each of those triggers a separate query
    # (typically 4 round-trips per /me/ hit). Eager-load the related
    # rows once so the serializer reads from memory.
    user = (
        User.objects
        .select_related(
            "trainer_profile",
            "client_profile__trainer",
            "solo_profile__assigned_workout_plan",
        )
        .get(pk=request.user.pk)
    )
    return Response(UserMeSerializer(user).data, status=status.HTTP_200_OK)


# -------------------------------------------------------------------
# Startup composite (task #29 / P.2)
#
# iOS used to fan out a dozen individual GETs on cold launch:
#   /me, /me/home-stats, /me/required-actions, /nutrition/me/today,
#   /nutrition/me/consumption, /progress/me/hydration,
#   /progress/me/next-checkin, /workouts/next, /workouts/plan/active,
#   /trophies/me, etc.
#
# Each round-trip carries TLS handshake + a Render cold-start tax.
# This composite endpoint folds the full launch payload into a
# single request so the app launches in ~one round-trip instead of
# twelve.
#
# Per-feature endpoints remain — pull-to-refresh and feature-scoped
# reloads still hit them. Composite is launch-only.
#
# Currently inlines (all done as of P.2):
#   user, home_stats, required_actions, nutrition.today,
#   nutrition.consumption, progress.hydration, progress.next_checkin,
#   workouts.next, workouts.plan_active, trophies.me, solo.
# -------------------------------------------------------------------


# -- per-namespace data builders ----------------------------------
#
# Each builder returns the payload its standalone endpoint would have
# returned (so iOS can decode into the same view model with no
# transformation), or `None` if the call blew up. None signals "fall
# back to the standalone endpoint" without crashing the launch.
# Wrapping defensively because a single bad data row in trophies
# shouldn't take down the user's whole app.


def _safely(fn):
    """Run a builder; swallow any exception, log it, return None."""
    try:
        return fn()
    except Exception:
        import logging
        logging.exception("startup_for_me sub-builder failed: %s", fn.__name__)
        return None


def _build_home_stats(user):
    """Latest body weight + streak + weekly target. Client-only."""
    if user.role != User.CLIENT or not hasattr(user, "client_profile"):
        return {"latest_weight_kg": None, "active_streak": 0, "weekly_target": 0}

    from apps.progress.models import CheckInAnswer
    from apps.users.dashboard_client_views import WEIGHT_FIELD_KEYS
    from apps.trophies.streak import compute_active_streak, weekly_target_for

    latest = (
        CheckInAnswer.objects
        .filter(
            submission__client=user,
            submission__status="submitted",
            value_number__isnull=False,
            question__field_key__in=WEIGHT_FIELD_KEYS,
        )
        .order_by("-submission__submitted_at")
        .values_list("value_number", flat=True)
        .first()
    )
    weekly_target = weekly_target_for(user)
    streak = compute_active_streak(user, weekly_target=weekly_target)
    return {
        "latest_weight_kg": round(float(latest), 1) if latest is not None else None,
        "active_streak":    streak,
        "weekly_target":    weekly_target,
    }


def _build_nutrition_today(user):
    """Today's plan + meals. Returns the same shape as
    /api/nutrition/me/today/. Client-only."""
    if user.role != User.CLIENT or not hasattr(user, "client_profile"):
        return {"status": "no_plan", "plan": None}

    plan = user.client_profile.assigned_nutrition_plan
    if plan is None:
        return {"status": "no_plan", "plan": None}

    from apps.nutrition.mobile_views import _meal_payload
    meals = list(plan.meals.all().prefetch_related("items"))
    meal_payloads = [_meal_payload(m) for m in meals]
    next_meal = meal_payloads[0] if meal_payloads else None
    return {
        "status": "assigned",
        "plan": {
            "id":              plan.id,
            "name":            plan.name,
            "calories_target": plan.calories_target,
            "protein_target":  plan.protein_target,
            "carbs_target":    plan.carbs_target,
            "fats_target":     plan.fats_target,
            "meals":           meal_payloads,
            "next_meal":       next_meal,
        },
    }


def _build_nutrition_consumption(user):
    """Today's ticks for the current client. Same shape as the GET on
    /api/nutrition/me/consumption/."""
    from django.utils import timezone
    from apps.nutrition.models import NutritionMealConsumption
    from apps.nutrition.mobile_views import _consumption_payload

    if user.role != User.CLIENT or not hasattr(user, "client_profile"):
        return {"date": timezone.localdate().isoformat(), "ticks": []}

    today = timezone.localdate()
    rows = (
        NutritionMealConsumption.objects
        .filter(client=user, consumed_on=today)
        .order_by("created_at")
    )
    return {"date": today.isoformat(), "ticks": [_consumption_payload(r) for r in rows]}


def _build_hydration(user):
    """Today's hydration row. Same shape as GET on
    /api/progress/me/hydration/."""
    from django.utils import timezone
    from apps.progress.models import HydrationLog

    if user.role != User.CLIENT or not hasattr(user, "client_profile"):
        return None

    today = timezone.localdate()
    log = HydrationLog.objects.filter(client=user, logged_on=today).first()
    return {
        "logged_on": today.isoformat(),
        "cups":      log.cups if log else 0,
        "goal_cups": log.goal_cups if log else 8,
    }


def _build_next_checkin(user):
    """Most-relevant check-in for the current client. Reuses the live
    view code path so logic stays single-sourced."""
    if user.role != User.CLIENT or not hasattr(user, "client_profile"):
        return {"status": "no_assignments"}

    # Re-use the existing view by constructing a synthetic request.
    # The view returns a DRF Response; pull `.data` off it.
    from apps.progress.mobile_views import next_checkin_for_me
    from rest_framework.request import Request
    from django.http import HttpRequest

    http_req = HttpRequest()
    http_req.method = "GET"
    http_req.user = user
    drf_req = Request(http_req)
    drf_req.user = user
    return next_checkin_for_me(drf_req).data


def _build_workout_next(user):
    """Next workout in the user's rotation. Same shape as
    /api/workouts/next/. Returns None when no plan assigned."""
    from apps.workouts.views import get_user_active_plan
    from apps.workouts.models import WorkoutSession
    from apps.workouts.serializers import WorkoutDaySerializer

    plan = get_user_active_plan(user)
    if plan is None:
        return None
    days = list(plan.days.all().order_by("order"))
    if not days:
        return None

    latest_session = (
        WorkoutSession.objects
        .filter(user=user, is_complete=True)
        .select_related("workout_day")
        .order_by("-completed_at")
        .first()
    )
    if latest_session is None:
        next_day = days[0]
    else:
        idx = next(
            (i for i, d in enumerate(days) if d.id == latest_session.workout_day_id),
            None,
        )
        next_day = days[0] if idx is None else days[(idx + 1) % len(days)]
    return WorkoutDaySerializer(next_day).data


def _build_workout_plan_active(user):
    """The active plan with all days. Mirrors /api/workouts/plan/active/."""
    from apps.workouts.views import get_user_active_plan
    from apps.workouts.serializers import WorkoutPlanSerializer

    plan = get_user_active_plan(user)
    if plan is None:
        return None
    return WorkoutPlanSerializer(plan).data


def _build_trophies(user):
    """Full trophy catalogue with earned + progress state. Same shape
    as /api/trophies/me/."""
    from apps.trophies.services import list_trophies_for
    return {"trophies": list_trophies_for(user)}


def _build_solo(user):
    """SOLO MVP — entitlement payload. Mirrors /api/users/solo/me/.
    iOS reads this on launch to decide whether to render Solo-mode
    screens, gate Pro features, etc."""
    from .models import SoloProfile

    is_solo = (user.role == User.SOLO)
    if not is_solo:
        return {
            "is_solo":         False,
            "tier":            None,
            "has_ai_access":   False,
            "has_pro_access":  False,
            "goals":           [],
            "experience":      "",
            "equipment":       "",
            "days_per_week":   0,
            "trial_ends_at":   None,
        }

    profile, _ = SoloProfile.objects.get_or_create(user=user)
    return {
        "is_solo":         True,
        "tier":            profile.tier,
        "has_ai_access":   profile.has_ai_access,
        "has_pro_access":  profile.has_pro_access,
        "goals":           profile.goals,
        "experience":      profile.experience,
        "equipment":       profile.equipment,
        "days_per_week":   profile.days_per_week,
        "trial_ends_at":   profile.trial_ends_at.isoformat() if profile.trial_ends_at else None,
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def startup_for_me(request):
    """Combined launch payload. iOS hits this once on cold launch
    instead of fanning out to ~10 standalone endpoints.

    Each namespace's payload mirrors the shape its standalone endpoint
    returns so iOS decodes into the same view model. Any namespace
    that fails returns null — iOS falls back to the standalone
    endpoint for that one namespace, but the rest of the launch
    still goes through.
    """
    from .profile_schema import missing_required_fields_for, needs_onboarding
    # PERF-STARTUP-SELECT-RELATED (May 2026, Deen QC) — same trick as
    # me_view. Every inline builder (`_build_nutrition_today`,
    # `_build_workout_next`, `_build_home_stats`) hits trainer / client
    # / solo profile fields; without eager-load each builder repeats
    # the same lazy fetches. Pulling them once at the top of the
    # composite saves 8–12 round-trips on cold launch.
    user = (
        User.objects
        .select_related(
            "trainer_profile",
            "client_profile__trainer",
            "solo_profile__assigned_workout_plan",
        )
        .get(pk=request.user.pk)
    )

    return Response(
        {
            # Always-on
            "user":              UserMeSerializer(user).data,
            "home_stats":        _safely(lambda: _build_home_stats(user))
                                 or {"latest_weight_kg": None, "active_streak": 0, "weekly_target": 0},
            "required_actions":  {
                "needs_onboarding":       needs_onboarding(user),
                "missing_profile_fields": missing_required_fields_for(user),
            },

            # Namespaces inlined for the cold-launch fan-out collapse
            "nutrition_today":       _safely(lambda: _build_nutrition_today(user)),
            "nutrition_consumption": _safely(lambda: _build_nutrition_consumption(user)),
            "hydration":             _safely(lambda: _build_hydration(user)),
            "next_checkin":          _safely(lambda: _build_next_checkin(user)),
            "workout_next":          _safely(lambda: _build_workout_next(user)),
            "workout_plan_active":   _safely(lambda: _build_workout_plan_active(user)),
            "trophies":              _safely(lambda: _build_trophies(user)),
            "solo":                  _safely(lambda: _build_solo(user)),
        },
        status=status.HTTP_200_OK,
    )


# -------------------------------------------------------------------
# Magic-link login (task #25 / L.1.1)
#
# Two endpoints:
#   • POST /api/users/magic-link/request/  {email}
#       Always returns 200 regardless of whether the email is on
#       file. Account-existence is leaked otherwise.
#   • POST /api/users/magic-link/verify/   {token}
#       Exchanges a single-use token for a DRF auth token + user
#       payload — same response shape as `login_view`.
#
# The link in the email is `afletics://magic/<token>` (custom URL
# scheme handled by `AfleticsApp.onOpenURL` on iOS) plus a
# `https://afletics.com/magic/<token>` web fallback for users
# who tap from a desktop / non-iOS browser.
# -------------------------------------------------------------------


def _magic_link_urls(token):
    """Return (deep_link, web_link) tuple for the email body.

    `deep_link` opens the iOS app via the new `afletics://` custom
    scheme. The legacy `afletics://` scheme is still registered on
    the iOS side for ~30 days so any in-flight emails from before
    the rebrand keep working — but every newly-issued link uses
    `afletics://` from now on.

    `web_link` is a fallback for desktop browsers and the
    eventual Universal Links setup.
    """
    # MAGIC-LINK-DOMAIN — our actual apex is afletics.com (the .app
    # one is owned by an unrelated party and 301-redirects to
    # elitehockeyhq.com, which 404'd every magic-link tap). The
    # bridge view at /magic/<token>/ is mounted on the Django
    # backend (config/urls.py); afletics.com is already pointed at
    # Render in DNS, so the same template + redirect-to-`afletics://`
    # flow works on the real apex.
    #
    # Override via the `AFLETICS_WEB_BASE_URL` env var on Render if
    # we ever swap apex domains again — no rebuild required.
    web_base = getattr(
        settings, "AFLETICS_WEB_BASE_URL",
        "https://afletics.com",
    )
    return (
        f"afletics://magic/{token}",
        f"{web_base}/magic/{token}/",
    )


def _client_ip(request):
    """Best-effort client IP for security forensics. Trusts
    X-Forwarded-For when behind Render's load balancer; falls back
    to REMOTE_ADDR otherwise."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


# ----------------------------------------------------------------------
# AUTO-HANDLE (May 2026, Deen QC) — memorable username generator.
#
# Old behaviour: derive username from email local-part. Worked but
# the resulting handles ("deenmali05", "j.smith") were boring and
# leaked the user's email partially. New behaviour: pick a random
# gym-themed prefix + animal noun (gymwhale, fitkangaroo, ironpanther)
# so handles read like a Spotify auto-generated playlist name.
#
# Collisions handled by appending an incrementing number suffix only
# when the bare two-word combo is taken. Vocabulary size is roughly
# 24 × 60 = 1,440 distinct combos before any suffix needed, which is
# plenty for the first several months. When the namespace fills up
# we expand the word lists; the suffix is the fallback that means
# we never refuse a signup.
# ----------------------------------------------------------------------
_HANDLE_PREFIXES = [
    "gym", "fit", "iron", "lift", "strong", "calm", "grit",
    "bold", "raw", "swift", "solid", "steady", "primal", "alpine",
    "pure", "tough", "lean", "deep", "wild", "lone", "neon",
    "amber", "noble", "kinetic",
]

_HANDLE_ANIMALS = [
    "whale", "kangaroo", "tiger", "eagle", "panther", "bear",
    "wolf", "falcon", "lion", "shark", "rhino", "bison",
    "moose", "stag", "elk", "lynx", "hawk", "raven",
    "otter", "puma", "cougar", "leopard", "jaguar", "cobra",
    "badger", "buffalo", "boar", "horse", "stallion", "bull",
    "gorilla", "ape", "orca", "marlin", "tuna", "trout",
    "salmon", "manta", "ray", "barracuda", "hammerhead",
    "owl", "kestrel", "harrier", "osprey", "albatross",
    "condor", "viper", "mamba", "python", "iguana", "gecko",
    "panda", "fox", "coyote", "jackal", "dingo",
    "mustang", "phoenix", "griffin",
]


def _generate_unique_handle(max_attempts: int = 20) -> str:
    """Return a free `User.username` of the form `<prefix><animal>`,
    falling back to `<prefix><animal><n>` when both random picks are
    already taken. The numeric fallback runs every iteration so even
    a wildly unlucky run still terminates quickly."""
    rng = secrets.SystemRandom()
    for _ in range(max_attempts):
        prefix = rng.choice(_HANDLE_PREFIXES)
        animal = rng.choice(_HANDLE_ANIMALS)
        candidate = f"{prefix}{animal}"
        if not User.objects.filter(username=candidate).exists():
            return candidate
        # Try numeric suffixes for this combo before re-rolling.
        for n in range(2, 30):
            with_suffix = f"{candidate}{n}"
            if not User.objects.filter(username=with_suffix).exists():
                return with_suffix
    # Pathological fallback — vocabulary fully exhausted. Cryptographic
    # random hex tail guarantees uniqueness even when the lists fill
    # up; user can always change their handle later.
    return f"lifter{secrets.token_hex(4)}"


@csrf_exempt
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def magic_link_request_view(request):
    """Send a one-tap sign-in link to `email`. If the address is
    new, also create a fresh Solo user on the fly so the same flow
    handles both login and signup. iOS detects the missing name
    post-auth and shows the name-capture step on first launch.

    Always responds 200 (well-formed input only — 400 on bad
    email). The success message is generic on purpose so attackers
    still can't probe for valid accounts via this endpoint.
    """
    email = (request.data.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return Response(
            {"detail": "Enter a valid email address."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        # ONBOARDING-QUICK-START — auto-create a Solo user so the
        # magic-link request works as the single signup-or-login
        # entry point. Username is now a memorable gym+animal combo
        # (gymwhale, fitkangaroo, ironpanther…) — see _generate_handle.
        # Password left unusable; magic link is the only auth path on
        # this account unless they later set one via the dashboard.
        username = _generate_unique_handle()

        # AUTO-FIRST-NAME (May 2026, Deen QC) — derive a friendly
        # default first name from the email so the post-signup Home
        # tile can greet the user without an extra prompt. Heuristics:
        #   1. If local-part contains a separator (".", "_", "-"),
        #      take the first chunk: "deen.ali@x" → "Deen".
        #   2. Otherwise use the whole local-part: "deen@x" → "Deen".
        #   3. Strip trailing digits ("deenmali05" → "Deenmali") and
        #      title-case. Numbers in real names are vanishingly rare.
        #   4. Fall back to empty string so the user can fill it in
        #      via Profile → Personal details if the inference is off.
        local_part = email.split("@", 1)[0]
        first_chunk = local_part
        for sep in (".", "_", "-"):
            if sep in first_chunk:
                first_chunk = first_chunk.split(sep, 1)[0]
                break
        stripped = first_chunk.rstrip("0123456789")
        derived_first = stripped.title()[:30] if stripped else ""

        user = User.objects.create(
            username=username,
            email=email,
            role=User.SOLO,
            first_name=derived_first,
        )
        user.set_unusable_password()
        user.save()

    # Generate a random URL-safe token. ~43 chars at entropy 256.
    token_str = secrets.token_urlsafe(32)
    record = MagicLoginToken.objects.create(
        user=user,
        token=token_str,
        requested_ip=_client_ip(request),
    )
    deep_link, web_link = _magic_link_urls(record.token)
    try:
        _send_magic_link_email(user=user, deep_link=deep_link, web_link=web_link)
    except Exception:
        # Don't leak email-send failures to the caller — the
        # attacker shouldn't be able to distinguish "we sent it"
        # from "we tried and failed". Surface to logs.
        import logging
        logging.exception("Magic-link email send failed for %s", email)

    return Response(
        {"detail": "A sign-in link is on its way. The link expires in 10 minutes."},
        status=status.HTTP_200_OK,
    )


# ----------------------------------------------------------------------
# APPLE-REVIEW-BYPASS + TEST-ACCOUNTS — helpers used by
# magic_link_verify_view. Kept module-private to avoid cluttering the
# import surface. See the docstring on magic_link_verify_view for the
# full design rationale.
# ----------------------------------------------------------------------
_BYPASS_VARIANT_SUFFIXES = {
    "reviewer": "",
    "day0":     "-day0",
    "day1":     "-day1",
    "reset":    "-reset",
}

_BYPASS_VARIANT_EMAILS = {
    "day0":  "day0@afletics.com",
    "day1":  "day1@afletics.com",
    "reset": "reset@afletics.com",
    # "reviewer" uses settings.APPLE_REVIEW_EMAIL — resolved at call
    # site so an operator override stays effective.
}


def _match_bypass_token(token_str: str, base_token: str) -> str | None:
    """Return which bypass variant the posted token matches, or None.

    Uses constant-time compare for each candidate so a timing oracle
    can't be used to recover the base token byte-by-byte.
    """
    for variant, suffix in _BYPASS_VARIANT_SUFFIXES.items():
        expected = f"{base_token}{suffix}"
        if secrets.compare_digest(token_str, expected):
            return variant
    return None


def _issue_bypass_signin(*, request, variant: str, reviewer_email: str):
    """Run the bypass-signin path for a recognised variant.

    Reviewer variant uses settings.APPLE_REVIEW_EMAIL. Day0/day1/reset
    use the canonical addresses. Reset additionally wipes the
    account's history BEFORE issuing the token so every sign-in lands
    a fresh new-user state. If the matched account doesn't exist on
    this deploy we fail closed and tell the operator (in logs) to run
    `python manage.py seed_reviewer_account`.
    """
    if variant == "reviewer":
        email = reviewer_email
    else:
        email = _BYPASS_VARIANT_EMAILS[variant]

    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        log.error(
            "BYPASS: %s token matched but account (%s) is missing. Run "
            "`python manage.py seed_reviewer_account` on this deploy.",
            variant, email,
        )
        return Response(
            {"detail": "This sign-in link expired or has already been used."},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    # RESET — wipe before issuing the token so the iOS app fetches a
    # cold state on its first /api/users/solo/me/ call. Local import
    # keeps test_account_seeds out of the normal-path import graph.
    if variant == "reset":
        from apps.users.test_account_seeds import wipe_test_account_history
        wipe_test_account_history(user)
        log.info("BYPASS: reset account history wiped (user_id=%s)", user.id)

    login(request, user)
    auth_token, _ = Token.objects.get_or_create(user=user)
    log.info("BYPASS: %s signed in (user_id=%s)", variant, user.id)
    return Response(
        {
            "message": "Magic link verified.",
            "token": auth_token.key,
            "user": UserMeSerializer(user).data,
        },
        status=status.HTTP_200_OK,
    )


@csrf_exempt
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def magic_link_verify_view(request):
    """Exchange a token for a DRF session. On success returns the
    same shape as `login_view` so iOS can drop it into
    `currentUser` without translation.

    APPLE-REVIEW-BYPASS + TEST-ACCOUNTS (2026-05-15) — App Store
    reviewers cannot receive magic-link emails, and Deen needs
    repeatable test accounts (Day 0 / Day 1 / reset-every-login)
    for QC. Four bypass tokens are recognised, all derived from
    a single APPLE_REVIEW_TOKEN env var:

      APPLE_REVIEW_TOKEN              → reviewer@afletics.com
                                        (Pro AI, ~30 days history)
      APPLE_REVIEW_TOKEN + "-day0"    → day0@afletics.com
                                        (Pro AI, empty cold-start)
      APPLE_REVIEW_TOKEN + "-day1"    → day1@afletics.com
                                        (Pro AI, 1 workout + 1 weight today)
      APPLE_REVIEW_TOKEN + "-reset"   → reset@afletics.com
                                        (Pro AI; wipes its own history
                                         BEFORE issuing the token so every
                                         sign-in lands a fresh new-user state)

    Reviewer is told (in App Store Connect → App Review Information
    → Notes) to open https://afletics.com/magic/<APPLE_REVIEW_TOKEN>/
    in Safari on the device. The existing web-bridge handler deep-
    links the iOS app, which posts the token here, which lands us in
    this branch. No iOS code change needed. To revoke, just unset
    the env var or rotate it.
    """
    token_str = (request.data.get("token") or "").strip()
    if not token_str:
        return Response(
            {"detail": "Missing token."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # APPLE-REVIEW-BYPASS + TEST-ACCOUNTS — checked BEFORE the DB
    # lookup so a leaked-but-rotated token never accidentally matches
    # a real MagicLoginToken row. Constant-time compare guards against
    # timing oracles even though the values are shared secrets, not
    # per-user credentials.
    review_token = getattr(settings, "APPLE_REVIEW_TOKEN", None) or ""
    review_email = getattr(settings, "APPLE_REVIEW_EMAIL", "reviewer@afletics.com")
    if review_token:
        bypass_match = _match_bypass_token(token_str, review_token)
        if bypass_match is not None:
            return _issue_bypass_signin(
                request=request,
                variant=bypass_match,
                reviewer_email=review_email,
            )

    record = MagicLoginToken.objects.filter(token=token_str).first()
    if record is None or not record.is_consumable:
        # Generic "expired or used" — don't differentiate so
        # someone holding an old token can't tell whether it ever
        # existed.
        return Response(
            {"detail": "This sign-in link expired or has already been used."},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    # Stamp the record before issuing the auth token so a race-
    # condition double-tap can't redeem twice.
    from django.utils import timezone
    record.used_at = timezone.now()
    record.consumed_ip = _client_ip(request)
    record.save(update_fields=["used_at", "consumed_ip"])

    user = record.user
    login(request, user)
    auth_token, _ = Token.objects.get_or_create(user=user)

    return Response(
        {
            "message": "Magic link verified.",
            "token": auth_token.key,
            "user": UserMeSerializer(user).data,
        },
        status=status.HTTP_200_OK,
    )


# ====================================================================
# EMAIL-EDIT — change-email flow
#
# Two endpoints:
#   1. email_change_request_view — user types a new email, we send
#      a 6-digit OTP to the NEW address.
#   2. email_change_confirm_view — user enters the OTP, we rotate
#      User.email and invalidate any other live codes.
#
# Why OTP not deep-link: keeps the user inside the iOS app (no
# context switch to the email client + back). Same pattern as
# Apple ID email change and most banking apps.
# ====================================================================
@csrf_exempt
@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def email_change_request_view(request):
    """Send a 6-digit verification code to a new email address.
    Validates the address is well-formed and not already in use by
    another account. Same-as-current is a no-op success."""
    new_email = (request.data.get("new_email") or "").strip().lower()
    if not new_email or "@" not in new_email or "." not in new_email.split("@")[1]:
        return Response(
            {"detail": "Enter a valid email address."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Same address — no-op success so the iOS UI doesn't have to
    # special-case it before sending.
    if new_email == (request.user.email or "").lower():
        return Response(
            {"detail": "That's already your current email.",
             "status": "unchanged"},
            status=status.HTTP_200_OK,
        )

    # Reject if another account is on this email. Generic message so
    # we don't leak whether an account exists.
    other = User.objects.filter(email__iexact=new_email).exclude(pk=request.user.pk).first()
    if other is not None:
        return Response(
            {"detail": "That email isn't available."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Generate the OTP. 6 digits, zero-padded so leading-zero codes
    # don't lose their digit count when displayed.
    code = f"{secrets.randbelow(1_000_000):06d}"

    # Invalidate any existing live codes for this user — if they
    # re-request, only the latest works.
    from .models import EmailChangeRequest
    from django.utils import timezone
    EmailChangeRequest.objects.filter(
        user=request.user, used_at__isnull=True,
    ).update(used_at=timezone.now())

    record = EmailChangeRequest.objects.create(
        user=request.user,
        new_email=new_email,
        code=code,
        requested_ip=_client_ip(request),
    )

    try:
        _send_email_change_otp(user=request.user, new_email=new_email, code=code)
    except Exception:
        import logging
        logging.exception("Email change OTP send failed for %s → %s",
                          request.user.username, new_email)

    return Response(
        {
            "detail": f"Verification code sent to {new_email}. Expires in {EmailChangeRequest.DEFAULT_TTL_MINUTES} minutes.",
            "status": "pending",
            "pending_email": new_email,
            "expires_at": record.expires_at.isoformat(),
        },
        status=status.HTTP_200_OK,
    )


@csrf_exempt
@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def email_change_confirm_view(request):
    """Confirm a 6-digit code and rotate User.email. Returns the
    updated user so iOS can refresh `currentUser` in-place."""
    code = (request.data.get("code") or "").strip()
    if not code or len(code) != 6 or not code.isdigit():
        return Response(
            {"detail": "Enter the 6-digit code."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    from .models import EmailChangeRequest
    from django.utils import timezone
    record = (
        EmailChangeRequest.objects
        .filter(user=request.user, code=code, used_at__isnull=True)
        .order_by("-created_at")
        .first()
    )
    if record is None or record.is_expired:
        return Response(
            {"detail": "That code is expired or wrong. Request a new one."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Race-safe: stamp the record BEFORE rotating the email so a
    # double-submit can't redeem twice.
    record.used_at = timezone.now()
    record.save(update_fields=["used_at"])

    # Last sanity check — another account may have taken this email
    # in the gap between request and confirm.
    if User.objects.filter(email__iexact=record.new_email).exclude(pk=request.user.pk).exists():
        return Response(
            {"detail": "That email is no longer available. Try a different one."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    request.user.email = record.new_email
    request.user.save(update_fields=["email"])

    return Response(
        {
            "detail": "Email updated.",
            "status": "ok",
            "user": UserMeSerializer(request.user).data,
        },
        status=status.HTTP_200_OK,
    )


def _send_email_change_otp(user, new_email, code):
    """Send the 6-digit OTP to the NEW address (where we're trying
    to verify ownership). Plain text — no template needed; the
    payload is tiny and the user reads it in the inbox preview."""
    subject = "Your Afletics verification code"
    body = (
        f"Hi,\n\n"
        f"Your Afletics email-change verification code is:\n\n"
        f"    {code}\n\n"
        f"This code expires in 15 minutes. If you didn't request this, "
        f"you can ignore this email.\n\n"
        f"— Afletics"
    )
    msg = EmailMultiAlternatives(
        subject=subject,
        body=body,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "Afletics <hello@afletics.com>"),
        to=[new_email],
    )
    msg.send(fail_silently=False)


def _send_magic_link_email(user, deep_link, web_link):
    """Render and send the magic-link email via Resend (handled by
    the existing custom email backend)."""
    # Subject line follows the Linear / Slack pattern — "[Brand]
    # sign-in link". Easier to spot in a packed inbox than a
    # generic "your link" framing.
    subject = "Afletics sign-in link"
    context = {
        "user": user,
        "deep_link": deep_link,
        "web_link": web_link,
        "ttl_minutes": MagicLoginToken.DEFAULT_TTL_MINUTES,
    }
    text_body = render_to_string("users/email/magic_link.txt", context)
    html_body = render_to_string("users/email/magic_link.html", context)
    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "Afletics <hello@afletics.com>"),
        to=[user.email],
    )
    msg.attach_alternative(html_body, "text/html")
    msg.send(fail_silently=False)


def magic_link_web_handler(request, token):
    """Web-side handler for `https://afletics.com/magic/<token>`.

    Three branches based on the token's owner:
      • TRAINER  — consume the token + create a Django session +
                   redirect straight to /dashboard. Trainers don't
                   have an iOS app, so the link IS the sign-in.
      • CLIENT on iOS — render a bridge page that meta-refreshes
                   to `afletics://magic/<token>` to open the app.
      • CLIENT elsewhere — friendly "open this on your phone"
                   page with App Store guidance.

    Email clients (Gmail in particular) rewrite custom schemes to
    https:// before exposing them as clickable, so we always send
    the https URL in the email and let this handler route.
    """
    from django.utils import timezone
    record = MagicLoginToken.objects.filter(token=token).first()

    # Trainer auto-login. Token must be valid (not expired, not
    # used) — if not, render the same bridge page so the user
    # sees the consistent friendly error rather than raw text.
    if record is not None and record.is_consumable and record.user.role == User.TRAINER:
        record.used_at = timezone.now()
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        record.consumed_ip = forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR")
        record.save(update_fields=["used_at", "consumed_ip"])
        from django.contrib.auth import login as django_login
        django_login(request, record.user)
        from django.shortcuts import redirect as _redirect
        return _redirect("trainer-hub-page")

    # Client / iOS deep-link bridge (existing behaviour).
    user_agent = request.META.get("HTTP_USER_AGENT", "").lower()
    is_ios = ("iphone" in user_agent) or ("ipad" in user_agent) or ("ipod" in user_agent)
    return render(
        request,
        "users/magic_link_bridge.html",
        {
            "deep_link": f"afletics://magic/{token}",
            "is_ios": is_ios,
            "ttl_minutes": MagicLoginToken.DEFAULT_TTL_MINUTES,
        },
    )


@api_view(["GET"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def home_stats_for_me(request):
    """Stats that drive the iOS Home stat-row tiles.

    Currently just `latest_weight_kg`, sourced from the most recent
    submitted check-in answer to a question with a weight `field_key`
    (`current_weight`, `daily_weight`, `weekly_weight`). Returns null
    when the client has no logged weights yet.

    Day streak is intentionally NOT computed server-side — iOS already
    has the full local workout-log store and computes streak client-
    side from there. Adding server-side streak would just duplicate
    the data and risk drift.

    Designed to be extensible — additional home stats can be folded
    into this single endpoint as we wire them up so iOS only needs one
    round-trip per home refresh.
    """
    # Local imports keep dashboard view dependencies out of this app's
    # public import surface, and avoid an apps.users → apps.progress
    # circular at module load time.
    from apps.progress.models import CheckInAnswer
    from apps.users.dashboard_client_views import WEIGHT_FIELD_KEYS

    user = request.user
    if user.role != User.CLIENT or not hasattr(user, "client_profile"):
        # Trainers don't have a personal Home stat-row, but returning
        # an empty payload keeps the iOS contract simple — no special-
        # case branching for non-clients on the device.
        return Response({"latest_weight_kg": None})

    latest = (
        CheckInAnswer.objects
        .filter(
            submission__client=user,
            submission__status="submitted",
            value_number__isnull=False,
            question__field_key__in=WEIGHT_FIELD_KEYS,
        )
        .order_by("-submission__submitted_at")
        .values_list("value_number", flat=True)
        .first()
    )

    # Rolling 7-day target streak — same definition the trophy
    # evaluators use, so the Home tile and the streak trophies always
    # agree. Falls back to a default weekly target when the user has
    # no assigned plan yet.
    from apps.trophies.streak import compute_active_streak, weekly_target_for
    weekly_target = weekly_target_for(user)
    streak = compute_active_streak(user, weekly_target=weekly_target)

    payload = {
        "latest_weight_kg": round(float(latest), 1) if latest is not None else None,
        "active_streak":    streak,
        "weekly_target":    weekly_target,
    }
    return Response(payload, status=status.HTTP_200_OK)


# -------------------------------------------------------------------
# Profile-completeness gate
#
# iOS calls /me/required-actions/ on login and uses the response to
# decide whether to surface:
#   1. A "supplemental profile" form for any system-required fields
#      the user hasn't filled (e.g. existing users without
#      date_of_birth after we added that requirement).
#   2. The trainer's onboarding form (if not yet submitted).
#
# Once everything's filled, the gate clears and MainTabView appears.
# -------------------------------------------------------------------


@csrf_exempt
@api_view(["GET"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def required_actions_for_me(request):
    """What does this user still owe before the app fully unlocks?

    Also returns the user's saved `personal_details` (name, DOB, sex,
    height, weight, primary goal, units) so the iOS Profile sheet
    pre-fills with whatever the user previously entered. Without
    this, the sheet always opened with blank fields and felt broken.
    """
    from .profile_schema import (
        missing_required_fields_for, needs_onboarding, personal_details_for,
    )
    user = request.user
    return Response({
        "needs_onboarding":       needs_onboarding(user),
        "missing_profile_fields": missing_required_fields_for(user),
        "personal_details":       personal_details_for(user),
    })


@csrf_exempt
@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def profile_update_for_me(request):
    """Update one or more system-required profile fields. Body is a
    plain JSON dict of {field_key: value}; iOS POSTs whatever the
    user filled in the supplemental form.

    Returns the same shape as `required_actions_for_me` so iOS can
    use one Decodable for both calls. `applied_fields` is included
    purely for debugging — iOS doesn't need to read it."""
    from .profile_schema import (
        apply_profile_update,
        missing_required_fields_for,
        needs_onboarding,
        personal_details_for,
    )
    user = request.user
    applied = apply_profile_update(user, request.data or {})
    return Response({
        "applied_fields":         applied,
        "needs_onboarding":       needs_onboarding(user),
        "missing_profile_fields": missing_required_fields_for(user),
        "personal_details":       personal_details_for(user),
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_client_view(request):
    if request.user.role != User.TRAINER or not hasattr(request.user, "trainer_profile"):
        return Response(
            {"detail": "Only trainers can create clients."},
            status=status.HTTP_403_FORBIDDEN,
        )

    serializer = ClientCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    user, client_profile = serializer.create_client_for_trainer(request.user)

    return Response(
        {
            "message": "Client created successfully.",
            "client": ClientListSerializer(user).data,
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def trainer_clients_view(request):
    if request.user.role != User.TRAINER or not hasattr(request.user, "trainer_profile"):
        return Response(
            {"detail": "Only trainers can view clients."},
            status=status.HTTP_403_FORBIDDEN,
        )

    client_users = User.objects.filter(
        role=User.CLIENT,
        client_profile__trainer=request.user.trainer_profile
    ).order_by("username")

    serializer = ClientListSerializer(client_users, many=True)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def assign_workout_plan_view(request):
    if request.user.role != User.TRAINER or not hasattr(request.user, "trainer_profile"):
        return Response(
            {"detail": "Only trainers can assign workout plans."},
            status=status.HTTP_403_FORBIDDEN,
        )

    serializer = AssignWorkoutPlanSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    client_user, client_profile = serializer.assign(request.user)

    return Response(
        {
            "message": "Workout plan assigned successfully.",
            "client": ClientListSerializer(client_user).data,
        },
        status=status.HTTP_200_OK,
    )
