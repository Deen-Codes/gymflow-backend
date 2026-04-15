from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import (
    WorkoutPlan,
    WorkoutDay,
    Exercise,
    WorkoutSession,
    ExerciseSession,
    SetPerformance,
)
from .serializers import (
    WorkoutPlanSerializer,
    WorkoutDaySerializer,
    WorkoutSessionSerializer,
    WorkoutSessionCreateSerializer,
)


def get_demo_user():
    """
    Temporary helper until proper authentication is added.
    """
    User = get_user_model()
    return User.objects.get(username="deen")


@api_view(["GET"])
def active_workout_plan(request):
    """
    Return the currently active workout plan for the demo user,
    including all workout days and exercises.
    """
    user = get_demo_user()

    plan = get_object_or_404(
        WorkoutPlan,
        user=user,
        is_active=True,
    )

    serializer = WorkoutPlanSerializer(plan)
    return Response(serializer.data)


@api_view(["GET"])
def workout_day_detail(request, day_id):
    """
    Return one workout day with all exercises and set targets.
    """
    user = get_demo_user()

    day = get_object_or_404(
        WorkoutDay.objects.select_related("plan"),
        id=day_id,
        plan__user=user,
    )

    serializer = WorkoutDaySerializer(day)
    return Response(serializer.data)


@api_view(["GET"])
def next_workout(request):
    """
    Return the next workout in the user's rotation.

    Logic:
    - if no completed sessions yet -> first workout day in plan order
    - else -> day after the most recently completed workout
    """
    user = get_demo_user()

    plan = get_object_or_404(
        WorkoutPlan,
        user=user,
        is_active=True,
    )

    days = list(plan.days.all().order_by("order"))

    if not days:
        return Response(
            {"detail": "No workout days found in active plan."},
            status=status.HTTP_404_NOT_FOUND,
        )

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
        current_day_id = latest_session.workout_day_id
        current_index = next(
            (index for index, day in enumerate(days) if day.id == current_day_id),
            None,
        )

        if current_index is None:
            next_day = days[0]
        else:
            next_day = days[(current_index + 1) % len(days)]

    serializer = WorkoutDaySerializer(next_day)
    return Response(serializer.data)


@api_view(["GET"])
def latest_workout_session_for_day(request, day_id):
    """
    Return the most recent logged session for a specific workout day.
    Useful for showing previous performance in the app.
    """
    user = get_demo_user()

    session = (
        WorkoutSession.objects
        .filter(user=user, workout_day_id=day_id)
        .prefetch_related("exercise_sessions__sets", "exercise_sessions__exercise")
        .order_by("-completed_at")
        .first()
    )

    if session is None:
        return Response(
            {"detail": "No previous session found for this workout day."},
            status=status.HTTP_404_NOT_FOUND,
        )

    serializer = WorkoutSessionSerializer(session)
    return Response(serializer.data)


@api_view(["POST"])
def create_workout_session(request):
    """
    Save a completed or incomplete workout session.

    Expected payload:
    {
      "workout_day_id": 1,
      "duration": 2450,
      "is_complete": true,
      "exercises": [
        {
          "exercise_id": 10,
          "sets": [
            {"set_number": 1, "weight": "40", "reps": "10"},
            {"set_number": 2, "weight": "42.5", "reps": "8"}
          ]
        }
      ]
    }
    """
    user = get_demo_user()

    serializer = WorkoutSessionCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    validated = serializer.validated_data

    workout_day = get_object_or_404(
        WorkoutDay,
        id=validated["workout_day_id"],
        plan__user=user,
    )

    workout_session = WorkoutSession.objects.create(
        user=user,
        workout_day=workout_day,
        completed_at=timezone.now(),
        duration=validated.get("duration", 0),
        is_complete=validated.get("is_complete", True),
    )

    for exercise_data in validated["exercises"]:
        exercise = get_object_or_404(
            Exercise,
            id=exercise_data["exercise_id"],
            workout_day=workout_day,
        )

        exercise_session = ExerciseSession.objects.create(
            workout_session=workout_session,
            exercise=exercise,
        )

        for set_data in exercise_data["sets"]:
            SetPerformance.objects.create(
                exercise_session=exercise_session,
                set_number=set_data["set_number"],
                weight=set_data.get("weight", ""),
                reps=set_data.get("reps", ""),
            )

    response_serializer = WorkoutSessionSerializer(workout_session)
    return Response(response_serializer.data, status=status.HTTP_201_CREATED)
