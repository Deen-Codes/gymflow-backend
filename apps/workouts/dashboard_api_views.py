"""Phase 1 trainer-dashboard JSON endpoints.

These power the drag-and-drop workout builder. They are deliberately
separate from the iOS-facing endpoints in `views.py` so the iOS API
surface stays small and stable while the dashboard evolves.

Auth model: a trainer is `request.user` with `role == TRAINER` and a
related `trainer_profile`. Catalog reads are open to any authenticated
trainer; writes are scoped to the calling trainer's own data.
"""
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.models import User

from .models import (
    Exercise,
    ExerciseCatalog,
    ExerciseLibraryItem,
    ExerciseSetTarget,
    WorkoutDay,
    WorkoutPlan,
)
from .dashboard_serializers import (
    DayExerciseCreateSerializer,
    DayExerciseReadSerializer,
    DayExerciseUpdateSerializer,
    DayReorderSerializer,
    ExerciseCatalogSerializer,
    ExerciseLibraryItemSerializer,
)


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------
def _require_trainer(request):
    """Return (None, error_response) on failure, (user, None) on success."""
    user = request.user
    if user.role != User.TRAINER or not hasattr(user, "trainer_profile"):
        return None, Response(
            {"detail": "Only trainers can use the dashboard API."},
            status=status.HTTP_403_FORBIDDEN,
        )
    return user, None


def _trainer_owns_day(trainer, day):
    """Trainer owns a day if they own its plan."""
    return day.plan.user_id == trainer.id


def _snapshot_catalog_into_library(trainer, catalog_item):
    """Copy a global ExerciseCatalog row into the trainer's per-trainer
    ExerciseLibraryItem table. Idempotent: if already snapshotted,
    returns the existing item.
    """
    existing = ExerciseLibraryItem.objects.filter(
        user=trainer, source_catalog_item=catalog_item
    ).first()
    if existing:
        return existing

    return ExerciseLibraryItem.objects.create(
        user=trainer,
        name=catalog_item.name,
        video_url=catalog_item.video_url,
        coaching_notes=catalog_item.instructions,
        muscle_group=catalog_item.muscle_group,
        equipment=catalog_item.equipment,
        source_catalog_item=catalog_item,
    )


def _next_label(workout_day):
    """Generate the next "A1" / "A2" / "B1" style label for a day.
    Cheap heuristic: count existing exercises and use A, B, C... by 5
    so trainers can rename freely without us re-numbering."""
    count = workout_day.exercises.count()
    letter = chr(ord("A") + (count // 5))
    return f"{letter}{(count % 5) + 1}"


# -------------------------------------------------------------------
# Catalog (right-rail search)
# -------------------------------------------------------------------
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def catalog_search(request):
    """GET /api/workouts/dashboard/catalog/?q=&muscle=&equipment=&limit=

    Free-text search over the global ExerciseCatalog. Default limit 50,
    capped at 200 so the right-rail can't accidentally pull the whole
    table on a typo.
    """
    trainer, err = _require_trainer(request)
    if err:
        return err

    q = (request.query_params.get("q") or "").strip()
    muscle = (request.query_params.get("muscle") or "").strip()
    equipment = (request.query_params.get("equipment") or "").strip()
    try:
        limit = min(int(request.query_params.get("limit", 50)), 200)
    except ValueError:
        limit = 50

    qs = ExerciseCatalog.objects.filter(is_published=True)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(muscle_group__icontains=q))
    if muscle:
        qs = qs.filter(muscle_group__iexact=muscle)
    if equipment:
        qs = qs.filter(equipment__iexact=equipment)

    qs = qs.order_by("name")[:limit]
    data = ExerciseCatalogSerializer(qs, many=True, context={"request": request}).data
    return Response({"results": data})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def catalog_facets(request):
    """GET /api/workouts/dashboard/catalog/facets/

    Distinct muscle_group / equipment values so the right-rail can
    render filter chips without hard-coding a list.
    """
    trainer, err = _require_trainer(request)
    if err:
        return err

    muscles = list(
        ExerciseCatalog.objects.filter(is_published=True)
        .exclude(muscle_group="")
        .values_list("muscle_group", flat=True)
        .distinct()
        .order_by("muscle_group")
    )
    equipment = list(
        ExerciseCatalog.objects.filter(is_published=True)
        .exclude(equipment="")
        .values_list("equipment", flat=True)
        .distinct()
        .order_by("equipment")
    )
    return Response({"muscle_groups": muscles, "equipment": equipment})


# -------------------------------------------------------------------
# Library (per-trainer snapshots)
# -------------------------------------------------------------------
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def library_list(request):
    """GET /api/workouts/dashboard/library/?q=

    The trainer's own ExerciseLibraryItems (catalog snapshots + custom
    items they created from scratch).
    """
    trainer, err = _require_trainer(request)
    if err:
        return err

    q = (request.query_params.get("q") or "").strip()
    qs = ExerciseLibraryItem.objects.filter(user=trainer)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(muscle_group__icontains=q))
    qs = qs.order_by("name")
    data = ExerciseLibraryItemSerializer(qs, many=True).data
    return Response({"results": data})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def library_snapshot_from_catalog(request):
    """POST /api/workouts/dashboard/library/snapshot/  body: {catalog_id}

    "Add to my library" — copies a global catalog entry into this
    trainer's library table. Idempotent.
    """
    trainer, err = _require_trainer(request)
    if err:
        return err

    catalog_id = request.data.get("catalog_id")
    if not catalog_id:
        return Response(
            {"detail": "catalog_id is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    catalog_item = get_object_or_404(
        ExerciseCatalog, pk=catalog_id, is_published=True
    )
    item = _snapshot_catalog_into_library(trainer, catalog_item)
    return Response(
        ExerciseLibraryItemSerializer(item).data,
        status=status.HTTP_201_CREATED,
    )


# -------------------------------------------------------------------
# Day-exercise CRUD (drag-drop builder)
# -------------------------------------------------------------------
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def day_add_exercise(request):
    """POST /api/workouts/dashboard/day-exercises/

    Drop a library item (or a catalog item — auto-snapshotted first)
    onto a workout day. Appended to the bottom; client reorders after.
    Default set_targets: 3 x "8-12" if none provided.
    """
    trainer, err = _require_trainer(request)
    if err:
        return err

    serializer = DayExerciseCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    payload = serializer.validated_data

    day = get_object_or_404(WorkoutDay, pk=payload["workout_day_id"])
    if not _trainer_owns_day(trainer, day):
        return Response({"detail": "Not your plan."}, status=status.HTTP_403_FORBIDDEN)

    # Resolve the source library item (snapshotting from catalog if
    # the trainer dragged straight from the catalog tab).
    if payload.get("library_item_id"):
        library_item = get_object_or_404(
            ExerciseLibraryItem, pk=payload["library_item_id"], user=trainer
        )
    else:
        catalog_item = get_object_or_404(
            ExerciseCatalog, pk=payload["catalog_id"], is_published=True
        )
        library_item = _snapshot_catalog_into_library(trainer, catalog_item)

    with transaction.atomic():
        order = day.exercises.count()
        exercise = Exercise.objects.create(
            workout_day=day,
            name=library_item.name,
            label=payload.get("label") or _next_label(day),
            order=order,
            superset_group=payload.get("superset_group"),
        )
        targets = payload.get("set_targets") or [
            {"set_number": i + 1, "reps": "8-12"} for i in range(3)
        ]
        ExerciseSetTarget.objects.bulk_create(
            [
                ExerciseSetTarget(
                    exercise=exercise,
                    set_number=t["set_number"],
                    reps=t["reps"],
                )
                for t in targets
            ]
        )

    return Response(
        DayExerciseReadSerializer(exercise).data,
        status=status.HTTP_201_CREATED,
    )


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def day_update_exercise(request, exercise_id):
    """PATCH /api/workouts/dashboard/day-exercises/<id>/"""
    trainer, err = _require_trainer(request)
    if err:
        return err

    exercise = get_object_or_404(Exercise, pk=exercise_id)
    if not _trainer_owns_day(trainer, exercise.workout_day):
        return Response({"detail": "Not your plan."}, status=status.HTTP_403_FORBIDDEN)

    serializer = DayExerciseUpdateSerializer(data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    payload = serializer.validated_data

    with transaction.atomic():
        if "label" in payload:
            exercise.label = payload["label"] or exercise.label
        if "superset_group" in payload:
            exercise.superset_group = payload["superset_group"]
        exercise.save()

        if "set_targets" in payload:
            exercise.sets.all().delete()
            ExerciseSetTarget.objects.bulk_create(
                [
                    ExerciseSetTarget(
                        exercise=exercise,
                        set_number=t["set_number"],
                        reps=t["reps"],
                    )
                    for t in payload["set_targets"]
                ]
            )

    return Response(DayExerciseReadSerializer(exercise).data)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def day_delete_exercise(request, exercise_id):
    """DELETE /api/workouts/dashboard/day-exercises/<id>/"""
    trainer, err = _require_trainer(request)
    if err:
        return err

    exercise = get_object_or_404(Exercise, pk=exercise_id)
    if not _trainer_owns_day(trainer, exercise.workout_day):
        return Response({"detail": "Not your plan."}, status=status.HTTP_403_FORBIDDEN)

    day = exercise.workout_day
    with transaction.atomic():
        exercise.delete()
        # Compact `order` so future reorders stay sane.
        for index, remaining in enumerate(day.exercises.order_by("order")):
            if remaining.order != index:
                remaining.order = index
                remaining.save(update_fields=["order"])

    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def day_reorder_exercises(request):
    """POST /api/workouts/dashboard/day-exercises/reorder/

    Body: {workout_day_id, ordered_exercise_ids: [...]}
    Validates that every ID belongs to the day before applying.
    """
    trainer, err = _require_trainer(request)
    if err:
        return err

    serializer = DayReorderSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    payload = serializer.validated_data

    day = get_object_or_404(WorkoutDay, pk=payload["workout_day_id"])
    if not _trainer_owns_day(trainer, day):
        return Response({"detail": "Not your plan."}, status=status.HTTP_403_FORBIDDEN)

    ids = payload["ordered_exercise_ids"]
    existing = list(day.exercises.values_list("id", flat=True))
    if set(ids) != set(existing):
        return Response(
            {"detail": "ordered_exercise_ids must contain exactly the day's exercises."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    with transaction.atomic():
        for index, exercise_id in enumerate(ids):
            Exercise.objects.filter(pk=exercise_id).update(order=index)

    refreshed = day.exercises.order_by("order").prefetch_related("sets")
    return Response(
        {"results": DayExerciseReadSerializer(refreshed, many=True).data}
    )
