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
def bootstrap_demo_data_view(request):
    from apps.workouts.models import WorkoutPlan

    trainer_username = "deen"
    trainer_email = "deenali3@outlook.com"
    trainer_password = "i9DRX786!"

    client_username = "client1"
    client_email = "client1@example.com"
    client_password = "testpass123"

    trainer_user, trainer_created = User.objects.get_or_create(
        username=trainer_username,
        defaults={
            "email": trainer_email,
            "role": User.TRAINER,
            "is_staff": True,
            "is_superuser": True,
        },
    )

    if trainer_created:
        trainer_user.set_password(trainer_password)
        trainer_user.save()
    else:
        trainer_user.email = trainer_email
        trainer_user.role = User.TRAINER
        trainer_user.is_staff = True
        trainer_user.is_superuser = True
        trainer_user.set_password(trainer_password)
        trainer_user.save()

    trainer_profile, _ = TrainerProfile.objects.get_or_create(
        user=trainer_user,
        defaults={
            "business_name": "Deen Ali Training",
            "slug": "deen",
        },
    )

    if trainer_profile.business_name != "Deen Ali Training" or trainer_profile.slug != "deen":
        trainer_profile.business_name = "Deen Ali Training"
        trainer_profile.slug = "deen"
        trainer_profile.save()

    client_user, client_created = User.objects.get_or_create(
        username=client_username,
        defaults={
            "email": client_email,
            "role": User.CLIENT,
        },
    )

    if client_created:
        client_user.set_password(client_password)
        client_user.save()
    else:
        client_user.email = client_email
        client_user.role = User.CLIENT
        client_user.set_password(client_password)
        client_user.save()

    client_profile, _ = ClientProfile.objects.get_or_create(
        user=client_user,
        defaults={
            "trainer": trainer_profile,
        },
    )

    if client_profile.trainer_id != trainer_profile.id:
        client_profile.trainer = trainer_profile
        client_profile.save()

    workout_plan = WorkoutPlan.objects.filter(user=trainer_user, is_active=True).first()

    if workout_plan:
        client_profile.assigned_workout_plan = workout_plan
        client_profile.save()

    return Response(
        {
            "message": "Bootstrap complete.",
            "trainer_username": trainer_user.username,
            "client_username": client_user.username,
            "assigned_workout_plan_id": workout_plan.id if workout_plan else None,
        },
        status=status.HTTP_200_OK,
    )
