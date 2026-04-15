from django.contrib.auth import login, logout
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework import status

from .models import User, TrainerProfile, ClientProfile
from .serializers import (
    LoginSerializer,
    UserMeSerializer,
    ClientCreateSerializer,
    ClientListSerializer,
    AssignWorkoutPlanSerializer,
)


@api_view(["POST"])
@permission_classes([AllowAny])
def login_view(request):
    serializer = LoginSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    user = serializer.validated_data["user"]
    login(request, user)

    return Response(
        {
            "message": "Login successful.",
            "user": UserMeSerializer(user).data,
        },
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def logout_view(request):
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


@api_view(["POST"])
@permission_classes([AllowAny])
def debug_reset_live_users_view(request):
    """
    TEMP ONLY.
    Resets live admin/trainer user and shows current users.
    Remove after use.
    """
    deen_password = "CHANGE_THIS_NOW_123!"

    deen_user, _ = User.objects.get_or_create(
        username="deen",
        defaults={
            "email": "deenali3@outlook.com",
            "role": User.TRAINER,
        },
    )

    deen_user.email = "deenali3@outlook.com"
    deen_user.role = User.TRAINER
    deen_user.is_staff = True
    deen_user.is_superuser = True
    deen_user.set_password(deen_password)
    deen_user.save()

    trainer_profile, _ = TrainerProfile.objects.get_or_create(
        user=deen_user,
        defaults={
            "business_name": "Deen Ali Training",
            "slug": "deen",
        },
    )

    users = list(
        User.objects.all().order_by("id").values(
            "id", "username", "email", "role", "is_staff", "is_superuser"
        )
    )

    return Response(
        {
            "message": "Live users reset complete.",
            "deen_password": deen_password,
            "trainer_profile_id": trainer_profile.id,
            "users": users,
        },
        status=status.HTTP_200_OK,
    )
