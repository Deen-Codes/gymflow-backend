from django.contrib.auth import login, logout
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework.authtoken.models import Token
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework import status

from .models import User
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
    payload = {
        "latest_weight_kg": round(float(latest), 1) if latest is not None else None,
    }
    return Response(payload, status=status.HTTP_200_OK)


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
