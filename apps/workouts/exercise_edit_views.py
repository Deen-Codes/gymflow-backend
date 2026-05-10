"""T2.8 — User-side edit endpoints for assigned programmes.

Lets a Solo user edit their assigned programme directly without
going through AI chat. Same data model as the existing AI mutation
handlers (apps/users/mutation_views.py), but called inline from
the iOS edit-mode UI rather than the propose/apply chat flow.

Every edit:
  • Stamps `Exercise.provenance = "user_edit"` (T1.9).
  • Writes a `RecentEditLog` row (T2.10) so the AI PT context
    surfaces the change on the next chat / weekly review.
  • Verifies the user owns the underlying programme — Solo users
    can't edit other users' plans.

Endpoints:
  • POST   /api/workouts/exercise/<id>/swap/   — swap to another catalog item
  • PATCH  /api/workouts/exercise/<id>/        — update sets/reps/rest
  • POST   /api/workouts/days/<id>/exercises/  — add an exercise to a day
"""
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import (
    api_view, authentication_classes, permission_classes,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.models import User
from apps.users.cross_domain_alignment import (
    alignment_chip_after_workout_change,
)
from .models import (
    Exercise, ExerciseCatalog, ExerciseSetTarget, WorkoutDay, WorkoutPlan,
)


# ----------------------------------------------------------------
# Permission helpers
# ----------------------------------------------------------------
def _user_owns_exercise(user, exercise: Exercise) -> bool:
    """Solo users own their assigned plan only. Trainer-track edits
    go through the dashboard, not this endpoint."""
    if user.role != User.SOLO:
        return False
    plan = exercise.workout_day.plan
    profile = getattr(user, "solo_profile", None)
    if profile is None:
        return False
    return profile.assigned_workout_plan_id == plan.id


def _user_owns_day(user, day: WorkoutDay) -> bool:
    if user.role != User.SOLO:
        return False
    profile = getattr(user, "solo_profile", None)
    if profile is None:
        return False
    return profile.assigned_workout_plan_id == day.plan_id


def _user_owns_plan(user, plan: WorkoutPlan) -> bool:
    if user.role != User.SOLO:
        return False
    profile = getattr(user, "solo_profile", None)
    if profile is None:
        return False
    return profile.assigned_workout_plan_id == plan.id


def _log_edit(user, kind: str, summary: str, payload: dict) -> None:
    """Best-effort RecentEditLog write."""
    try:
        from apps.users.models import RecentEditLog
        RecentEditLog.record(user=user, kind=kind, summary=summary, payload=payload)
    except Exception:
        pass


# ----------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------
@api_view(["PATCH"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def exercise_edit_view(request, exercise_id: int):
    """Edit sets count / reps target / rest_seconds on an existing
    Exercise row. Body: {"sets": [{"set_number": 1, "reps": "8-12"}],
    "rest_seconds": 90}. Either field optional.

    Sets array, when provided, fully replaces the existing
    `ExerciseSetTarget` rows — simpler than a partial-merge protocol
    and matches how iOS edit mode tends to author the whole-day list
    at once.
    """
    ex = get_object_or_404(Exercise, pk=exercise_id)
    if not _user_owns_exercise(request.user, ex):
        return Response({"detail": "Not your exercise."}, status=status.HTTP_403_FORBIDDEN)

    body = request.data or {}
    summary_parts = []

    with transaction.atomic():
        if "rest_seconds" in body:
            try:
                rest = int(body["rest_seconds"])
                if 0 <= rest <= 600:
                    ex.rest_seconds = rest
                    summary_parts.append(f"rest→{rest}s")
            except (TypeError, ValueError):
                pass

        sets = body.get("sets")
        if isinstance(sets, list):
            ExerciseSetTarget.objects.filter(exercise=ex).delete()
            for set_idx, st in enumerate(sets):
                if not isinstance(st, dict):
                    continue
                set_number = int(st.get("set_number") or (set_idx + 1))
                reps = (st.get("reps") or "")[:20]
                ExerciseSetTarget.objects.create(
                    exercise=ex,
                    set_number=set_number,
                    reps=reps,
                )
            summary_parts.append(f"sets→{len(sets)}")

        ex.provenance = Exercise.PROVENANCE_USER
        ex.save()

    if summary_parts:
        _log_edit(
            request.user,
            kind="workout_set",
            summary=f"{ex.name}: {', '.join(summary_parts)}",
            payload={"exercise_id": ex.id, "changes": summary_parts},
        )

    return Response({
        "ok": True,
        "exercise_id": ex.id,
        "rest_seconds": ex.rest_seconds,
        "set_count": ex.sets.count(),
        # T4.2 — sets/reps/rest edits don't rebalance the
        # cross-domain ledger meaningfully on their own. Reserved
        # field for future heavy-volume adjustments (e.g. doubling
        # the set count on a leg day).
        "chip": None,
    })


@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def exercise_swap_view(request, exercise_id: int):
    """Swap an Exercise to a different ExerciseCatalog row. Body:
    {"catalog_id": 1234}. Updates name + catalog_item + provenance,
    preserves sets/reps/rest. Logs a workout_swap RecentEditLog row."""
    ex = get_object_or_404(Exercise, pk=exercise_id)
    if not _user_owns_exercise(request.user, ex):
        return Response({"detail": "Not your exercise."}, status=status.HTTP_403_FORBIDDEN)

    catalog_id = (request.data or {}).get("catalog_id")
    if not catalog_id:
        return Response({"detail": "catalog_id required."}, status=400)

    catalog = ExerciseCatalog.objects.filter(pk=catalog_id, is_published=True).first()
    if catalog is None:
        return Response({"detail": "Unknown catalog id."}, status=404)

    old_name = ex.name
    ex.name = catalog.name[:255]
    ex.catalog_item = catalog
    ex.provenance = Exercise.PROVENANCE_USER
    ex.save()

    _log_edit(
        request.user,
        kind="workout_swap",
        summary=f"{old_name} → {catalog.name}",
        payload={
            "exercise_id":      ex.id,
            "old_name":         old_name,
            "new_name":         catalog.name,
            "new_catalog_id":   catalog.id,
        },
    )

    return Response({
        "ok": True,
        "exercise_id": ex.id,
        "name":         ex.name,
        "catalog_id":   catalog.id,
        # T4.2 — pure swap (same slot in the day) doesn't shift the
        # weekly volume / frequency. Returns chip=null. Same field
        # shape as the patch / add endpoints so iOS can read this
        # uniformly.
        "chip": None,
    })


@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def workout_day_add_exercise_view(request, day_id: int):
    """Add a new Exercise row to a WorkoutDay. Body:
    {"catalog_id": 1234, "label": "C", "sets": [{"set_number": 1, "reps": "8-12"}]}
    sets optional — defaults to 3×8-12 if omitted.

    Position appended to end of day. Provenance stamped user_edit."""
    day = get_object_or_404(WorkoutDay, pk=day_id)
    if not _user_owns_day(request.user, day):
        return Response({"detail": "Not your day."}, status=status.HTTP_403_FORBIDDEN)

    body = request.data or {}
    catalog_id = body.get("catalog_id")
    catalog = None
    if catalog_id:
        catalog = ExerciseCatalog.objects.filter(pk=catalog_id, is_published=True).first()
        if catalog is None:
            return Response({"detail": "Unknown catalog id."}, status=404)

    if not catalog:
        return Response({"detail": "catalog_id required."}, status=400)

    next_order = (
        Exercise.objects.filter(workout_day=day)
        .order_by("-order").values_list("order", flat=True).first() or 0
    ) + 1
    label = (body.get("label") or chr(ord("A") + (next_order - 1)))[:10]

    with transaction.atomic():
        ex = Exercise.objects.create(
            workout_day=day,
            name=catalog.name[:255],
            label=label,
            order=next_order,
            catalog_item=catalog,
            provenance=Exercise.PROVENANCE_USER,
            rest_seconds=int(body.get("rest_seconds") or 90),
        )
        sets = body.get("sets") or [
            {"set_number": 1, "reps": "8-12"},
            {"set_number": 2, "reps": "8-12"},
            {"set_number": 3, "reps": "8-12"},
        ]
        for set_idx, st in enumerate(sets):
            if not isinstance(st, dict):
                continue
            ExerciseSetTarget.objects.create(
                exercise=ex,
                set_number=int(st.get("set_number") or (set_idx + 1)),
                reps=(st.get("reps") or "")[:20],
            )

    _log_edit(
        request.user,
        kind="workout_add",
        summary=f"+{catalog.name} on {day.title}",
        payload={
            "exercise_id":  ex.id,
            "catalog_id":   catalog.id,
            "day_id":       day.id,
        },
    )

    # T4.2 — adding an exercise alone doesn't shift weekly volume
    # by enough to surface a cross-domain chip; it's the day-count
    # changes (covered by future "add day" / "remove day" endpoints)
    # and big macro shifts that trigger one. Returning the optional
    # chip slot here so iOS doesn't need to switch on response shape
    # between endpoints — `chip` is null for this path.
    response_payload: dict = {
        "ok": True,
        "exercise_id": ex.id,
        "name":         ex.name,
        "chip":         None,
    }
    return Response(response_payload, status=status.HTTP_201_CREATED)


@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def workout_day_add_view(request, plan_id: int):
    """Add a new training day to the user's assigned plan. Body:
    {"title": "Day 5 — Arms"}. Title optional — defaults to
    "Day N" where N is the next sequence number.

    Returns the new day + a cross-domain chip (T4.2) since adding
    training days typically warrants a kcal target bump."""
    plan = get_object_or_404(WorkoutPlan, pk=plan_id)
    if not _user_owns_plan(request.user, plan):
        return Response({"detail": "Not your plan."}, status=status.HTTP_403_FORBIDDEN)

    body = request.data or {}
    next_order = (
        WorkoutDay.objects.filter(plan=plan)
        .order_by("-order").values_list("order", flat=True).first() or 0
    ) + 1
    title = (body.get("title") or f"Day {next_order}")[:100]

    with transaction.atomic():
        day = WorkoutDay.objects.create(
            plan=plan,
            title=title,
            order=next_order,
        )

    _log_edit(
        request.user,
        kind="workout_add_day",
        summary=f"+training day: {title}",
        payload={"plan_id": plan.id, "day_id": day.id, "title": title},
    )

    # T4.2 — adding a day shifts weekly volume meaningfully; surface
    # a kcal-bump chip so the user can keep nutrition in step.
    chip = alignment_chip_after_workout_change(request.user, day_added=1)

    return Response({
        "ok":    True,
        "day": {
            "id":    day.id,
            "title": day.title,
            "order": day.order,
        },
        "chip":  chip,
    }, status=status.HTTP_201_CREATED)


@api_view(["DELETE"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def workout_day_delete_view(request, day_id: int):
    """Remove a training day from the user's assigned plan. Cascades
    to the day's exercises + set targets. Returns a cross-domain chip
    suggesting a kcal trim (T4.2)."""
    day = get_object_or_404(WorkoutDay, pk=day_id)
    if not _user_owns_day(request.user, day):
        return Response({"detail": "Not your day."}, status=status.HTTP_403_FORBIDDEN)

    plan_id = day.plan_id
    title = day.title

    with transaction.atomic():
        day.delete()

    _log_edit(
        request.user,
        kind="workout_remove_day",
        summary=f"-training day: {title}",
        payload={"plan_id": plan_id, "title": title},
    )

    chip = alignment_chip_after_workout_change(request.user, day_removed=1)

    return Response({
        "ok":   True,
        "chip": chip,
    })
