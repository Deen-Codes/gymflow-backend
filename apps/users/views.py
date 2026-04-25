from django.contrib.auth import login, logout
from rest_framework.authtoken.models import Token
from rest_framework.decorators import api_view, permission_classes
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
@api_view(["POST"])
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


@api_view(["POST"])
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
