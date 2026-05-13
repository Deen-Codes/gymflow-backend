import logging

from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

log = logging.getLogger(__name__)

from apps.users.models import User
from .models import (
    WorkoutPlan,
    WorkoutDay,
    Exercise,
    ExerciseCatalog,
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


def get_user_active_plan(user):
    """
    Resolve which workout plan should be used for the authenticated user.

    Trainer:
        uses their own active plan
    Client:
        uses their assigned workout plan
    Solo (E.1):
        uses the programme they picked from the catalog
    """
    if user.role == User.TRAINER:
        return get_object_or_404(
            WorkoutPlan,
            user=user,
            is_active=True,
        )

    if user.role == User.CLIENT:
        if not hasattr(user, "client_profile") or not user.client_profile.assigned_workout_plan:
            return None
        return user.client_profile.assigned_workout_plan

    if user.role == User.SOLO:
        if not hasattr(user, "solo_profile") or not user.solo_profile.assigned_workout_plan:
            return None
        return user.solo_profile.assigned_workout_plan

    return None


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def active_workout_plan(request):
    """
    Return the active workout plan for the authenticated user.

    Trainer -> their own active plan
    Client -> their assigned workout plan
    """
    plan = get_user_active_plan(request.user)

    if plan is None:
        return Response(
            {"detail": "No workout plan assigned."},
            status=status.HTTP_404_NOT_FOUND,
        )

    serializer = WorkoutPlanSerializer(plan)
    return Response(serializer.data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def workout_day_detail(request, day_id):
    """
    Return one workout day with all exercises and set targets,
    only if it belongs to the authenticated user's resolved plan.
    """
    plan = get_user_active_plan(request.user)

    if plan is None:
        return Response(
            {"detail": "No workout plan assigned."},
            status=status.HTTP_404_NOT_FOUND,
        )

    day = get_object_or_404(
        WorkoutDay.objects.select_related("plan"),
        id=day_id,
        plan=plan,
    )

    serializer = WorkoutDaySerializer(day)
    return Response(serializer.data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def next_workout(request):
    """
    Return the next workout in the authenticated user's rotation.

    Logic:
    - if no completed sessions yet -> first workout day in plan order
    - else -> day after the most recently completed completed workout
    """
    user = request.user
    plan = get_user_active_plan(user)

    if plan is None:
        return Response(
            {"detail": "No workout plan assigned."},
            status=status.HTTP_404_NOT_FOUND,
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
@permission_classes([IsAuthenticated])
def latest_workout_session_for_day(request, day_id):
    """
    Return the most recent logged session for a specific workout day
    for the authenticated user only.
    """
    user = request.user
    plan = get_user_active_plan(user)

    if plan is None:
        return Response(
            {"detail": "No workout plan assigned."},
            status=status.HTTP_404_NOT_FOUND,
        )

    # ensure this day belongs to the user's plan
    get_object_or_404(WorkoutDay, id=day_id, plan=plan)

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


@csrf_exempt
@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def create_workout_session(request):
    """
    Save a completed or incomplete workout session for the authenticated user.

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
    user = request.user
    plan = get_user_active_plan(user)

    if plan is None:
        return Response(
            {"detail": "No workout plan assigned."},
            status=status.HTTP_404_NOT_FOUND,
        )

    serializer = WorkoutSessionCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    validated = serializer.validated_data

    workout_day = get_object_or_404(
        WorkoutDay,
        id=validated["workout_day_id"],
        plan=plan,
    )

    workout_session = WorkoutSession.objects.create(
        user=user,
        workout_day=workout_day,
        completed_at=timezone.now(),
        duration=validated.get("duration", 0),
        is_complete=validated.get("is_complete", True),
        notes=(validated.get("notes") or "").strip(),
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

    # Trophy evaluation — runs after the session + sets are persisted
    # so volume/rep/streak evaluators see the new data. Imported lazily
    # to avoid an apps.workouts → apps.trophies hard dependency at
    # module load time. Wrapped in a defensive try so a buggy
    # evaluator can never fail an otherwise-successful workout save.
    newly_earned = []
    try:
        from apps.trophies.services import evaluate_and_award
        for trophy in evaluate_and_award(user):
            newly_earned.append({
                "code":     trophy.code,
                "name":     trophy.name,
                "rarity":   trophy.rarity,
                "icon":     trophy.icon,
                "category": trophy.category,
            })
    except Exception:
        # Trophy eval must never fail the workout-save request.
        # log.exception captures full traceback so we can debug
        # which evaluator broke without losing the user's session.
        log.exception("trophies post-workout evaluation failed")

    response_serializer = WorkoutSessionSerializer(workout_session)
    payload = response_serializer.data
    # Append newly-earned trophies so the iOS workout-complete screen
    # can reveal them in the same response — no extra round-trip.
    payload["newly_earned_trophies"] = newly_earned
    return Response(payload, status=status.HTTP_201_CREATED)


# --------------------------------------------------------------------
# V0-LIMIT-3 — ad-hoc (plan-less) workout session create.
#
# Mirrors `create_workout_session` but for the iOS as-you-go flow
# where the user has no assigned plan + freely picked their lifts
# from the catalog (or typed names). Stores:
#   • WorkoutSession with workout_day=NULL + title=<free-form>
#   • ExerciseSession rows with exercise=NULL, name=<lift name>,
#     catalog=<optional FK to ExerciseCatalog>
#   • SetPerformance rows for each set
#
# Trophy evaluation runs the same as the plan-mode endpoint, so
# ad-hoc sessions count toward streak / volume / lifetime trophies.
# --------------------------------------------------------------------
@csrf_exempt
@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def create_workout_session_adhoc(request):
    """Save an ad-hoc workout session (no workout_day FK).

    Expected payload:
    {
      "title": "Push session",
      "duration": 1834,
      "is_complete": true,
      "exercises": [
        {
          "name": "Bench Press",
          "catalog_id": 42,           // optional
          "sets": [
            {"set_number": 1, "weight": "60", "reps": "8"},
            {"set_number": 2, "weight": "60", "reps": "8"}
          ]
        }
      ]
    }

    Returns the same shape as `create_workout_session` (id +
    newly_earned_trophies) so the iOS client can decode it with the
    same `CreateWorkoutSessionResponse` model. workout_day_id is
    null in the response for ad-hoc sessions.
    """
    user = request.user
    data = request.data or {}

    title = (data.get("title") or "").strip()[:255]
    if not title:
        title = "Today"  # sensible fallback if iOS sends an empty title

    try:
        duration = int(data.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0
    duration = max(0, duration)

    is_complete = bool(data.get("is_complete", True))
    exercises = data.get("exercises") or []
    if not isinstance(exercises, list):
        return Response(
            {"detail": "exercises must be a list."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    workout_session = WorkoutSession.objects.create(
        user=user,
        workout_day=None,
        title=title,
        completed_at=timezone.now(),
        duration=duration,
        is_complete=is_complete,
        notes="",
    )

    for ex_data in exercises:
        if not isinstance(ex_data, dict):
            continue

        ex_name = (ex_data.get("name") or "").strip()[:255]
        if not ex_name:
            # An ad-hoc lift must have a name. Skip silently rather
            # than 400 the whole request — the rest of the session
            # is still worth persisting.
            continue

        catalog = None
        catalog_id = ex_data.get("catalog_id")
        if catalog_id is not None:
            try:
                catalog = ExerciseCatalog.objects.filter(id=int(catalog_id)).first()
            except (TypeError, ValueError):
                catalog = None

        exercise_session = ExerciseSession.objects.create(
            workout_session=workout_session,
            exercise=None,
            name=ex_name,
            catalog=catalog,
        )

        for set_data in ex_data.get("sets") or []:
            if not isinstance(set_data, dict):
                continue
            try:
                set_number = int(set_data.get("set_number") or 1)
            except (TypeError, ValueError):
                set_number = 1
            SetPerformance.objects.create(
                exercise_session=exercise_session,
                set_number=set_number,
                weight=str(set_data.get("weight") or "")[:20],
                reps=str(set_data.get("reps") or "")[:20],
            )

    # Trophy evaluation — same lazy import + defensive try as the
    # plan-mode endpoint. Ad-hoc sessions count for streak / volume
    # / lifetime trophies just like plan sessions.
    newly_earned = []
    try:
        from apps.trophies.services import evaluate_and_award
        for trophy in evaluate_and_award(user):
            newly_earned.append({
                "code":     trophy.code,
                "name":     trophy.name,
                "rarity":   trophy.rarity,
                "icon":     trophy.icon,
                "category": trophy.category,
            })
    except Exception:
        log.exception("trophies post-workout (ad-hoc) evaluation failed")

    # Minimal response — matches the existing plan-mode shape so the
    # iOS `CreateWorkoutSessionResponse` decoder works for both
    # paths. workout_day_id is null because there isn't one.
    return Response(
        {
            "id": workout_session.id,
            "workout_day_id": None,
            "title": workout_session.title,
            "duration": workout_session.duration,
            "is_complete": workout_session.is_complete,
            "completed_at": workout_session.completed_at.isoformat(),
            "newly_earned_trophies": newly_earned,
        },
        status=status.HTTP_201_CREATED,
    )


# WORKOUT-NOTES-POSTSESSION — PATCH endpoint that updates the
# free-text "anything else?" note on an existing session. Used
# by the post-cinematic prompt: the session is created on
# Finish (notes empty), the celebration plays, and on Done
# the iOS client PATCHes notes here. Skip → no PATCH at all.
@api_view(["PATCH"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def update_workout_session_notes(request, session_id):
    """PATCH /api/workouts/sessions/<int:session_id>/notes/

    Accepts an optional `notes` (free-text), `rpe` (int 1–10) and
    `mood` (short string) per R7-2 (#59). All three fields are
    optional and partial — a client that only sends `notes` or only
    sends `rpe` is fine. Backwards-compatible with older clients
    that PATCHed only `notes`.
    """
    user = request.user
    session_obj = get_object_or_404(
        WorkoutSession, id=session_id, user=user,
    )

    update_fields = []

    if "notes" in request.data:
        notes = request.data.get("notes", "")
        if not isinstance(notes, str):
            return Response(
                {"detail": "notes must be a string."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        session_obj.notes = notes.strip()[:2000]
        update_fields.append("notes")

    if "rpe" in request.data:
        rpe = request.data.get("rpe")
        # Allow null to clear, int 1–10 to set. Anything else → 400.
        if rpe is None:
            session_obj.rpe = None
        elif isinstance(rpe, int) and 1 <= rpe <= 10:
            session_obj.rpe = rpe
        else:
            return Response(
                {"detail": "rpe must be null or an integer 1–10."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        update_fields.append("rpe")

    if "mood" in request.data:
        mood = request.data.get("mood", "")
        if not isinstance(mood, str):
            return Response(
                {"detail": "mood must be a string."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Schemaless on purpose — see model docstring. Trim + cap
        # but accept any short label so iOS can iterate without a
        # backend deploy.
        session_obj.mood = mood.strip()[:16]
        update_fields.append("mood")

    if update_fields:
        session_obj.save(update_fields=update_fields)

    return Response(
        {
            "id": session_obj.id,
            "notes": session_obj.notes,
            "rpe": session_obj.rpe,
            "mood": session_obj.mood,
        },
        status=status.HTTP_200_OK,
    )
