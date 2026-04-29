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
        pass

    logout(request)
    return Response({"message": "Logout successful."}, status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def me_view(request):
    return Response(UserMeSerializer(request.user).data, status=status.HTTP_200_OK)


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
# The link in the email is `gymflow://magic/<token>` (custom URL
# scheme handled by `GymFlowApp.onOpenURL` on iOS) plus a
# `https://gymflow.coach/magic/<token>` web fallback for users
# who tap from a desktop / non-iOS browser.
# -------------------------------------------------------------------


def _magic_link_urls(token):
    """Return (deep_link, web_link) tuple for the email body.

    `deep_link` opens the iOS app via the registered `gymflow://`
    custom scheme. `web_link` is a fallback for desktop browsers
    and the eventual Universal Links setup.
    """
    web_base = getattr(settings, "GYMFLOW_WEB_BASE_URL", "https://gymflow.coach")
    return (
        f"gymflow://magic/{token}",
        f"{web_base}/magic/{token}",
    )


def _client_ip(request):
    """Best-effort client IP for security forensics. Trusts
    X-Forwarded-For when behind Render's load balancer; falls back
    to REMOTE_ADDR otherwise."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


@csrf_exempt
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def magic_link_request_view(request):
    """Send a one-tap sign-in link to `email` if the address is on
    file. Always responds 200 — the success message is the same
    whether or not we recognised the email so attackers can't probe
    for valid accounts via this endpoint."""
    email = (request.data.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return Response(
            {"detail": "Enter a valid email address."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user = User.objects.filter(email__iexact=email).first()
    if user is not None:
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
        {"detail": "If that email is on file, a sign-in link is on its way. The link expires in 10 minutes."},
        status=status.HTTP_200_OK,
    )


@csrf_exempt
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def magic_link_verify_view(request):
    """Exchange a token for a DRF session. On success returns the
    same shape as `login_view` so iOS can drop it into
    `currentUser` without translation."""
    token_str = (request.data.get("token") or "").strip()
    if not token_str:
        return Response(
            {"detail": "Missing token."},
            status=status.HTTP_400_BAD_REQUEST,
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


def _send_magic_link_email(user, deep_link, web_link):
    """Render and send the magic-link email via Resend (handled by
    the existing custom email backend)."""
    subject = "Your GymFlow sign-in link"
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
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "GymFlow <hello@gymflow.coach>"),
        to=[user.email],
    )
    msg.attach_alternative(html_body, "text/html")
    msg.send(fail_silently=False)


def magic_link_web_handler(request, token):
    """Web-side handler for `https://gymflow.coach/magic/<token>`.

    Why this exists: the email's primary CTA points at
    `gymflow://magic/<token>` (custom URL scheme that launches the
    iOS app directly). But Gmail and a few other email clients
    silently rewrite custom schemes to https:// before exposing
    them as clickable, sending users to the gymflow.coach web URL
    instead. Without this view that's a 404.

    Strategy:
      • iOS visitor — render a tiny bridge page with a meta
        refresh to `gymflow://magic/<token>` so the app opens
        seamlessly. Falls back to a tap-to-open button if the
        meta refresh is blocked.
      • Anyone else — friendly "open this on your phone" page
        with App Store guidance. We don't try to authenticate
        web-side because the iOS app is the only client.

    The token isn't validated here — that's the verify endpoint's
    job. We just route the user back to the right place.
    """
    user_agent = request.META.get("HTTP_USER_AGENT", "").lower()
    is_ios = ("iphone" in user_agent) or ("ipad" in user_agent) or ("ipod" in user_agent)
    return render(
        request,
        "users/magic_link_bridge.html",
        {
            "deep_link": f"gymflow://magic/{token}",
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
    """What does this user still owe before the app fully unlocks?"""
    from .profile_schema import missing_required_fields_for, needs_onboarding
    user = request.user
    return Response({
        "needs_onboarding":       needs_onboarding(user),
        "missing_profile_fields": missing_required_fields_for(user),
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
    )
    user = request.user
    applied = apply_profile_update(user, request.data or {})
    return Response({
        "applied_fields":         applied,
        "needs_onboarding":       needs_onboarding(user),
        "missing_profile_fields": missing_required_fields_for(user),
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
