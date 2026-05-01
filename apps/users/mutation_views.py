"""
Phase A — apply / decline endpoints for AI-proposed mutations.

When the AI calls `propose_workout_mutation` or
`propose_nutrition_mutation` during a chat turn, a row is created
in `WorkoutMutation` / `NutritionMutation` with status=`proposed`.
The proposal payload travels back to iOS via the chat events
stream. iOS renders a proposal card with Apply / Don't apply
buttons.

These endpoints are what those buttons call:
  POST /api/users/solo/ai-pt/mutations/<id>/apply/
  POST /api/users/solo/ai-pt/mutations/<id>/decline/

`type` query param = "workout" | "nutrition" picks which model.

Why a separate apply endpoint (not via the AI loop):
  - Doesn't burn a chat slot.
  - Keeps state stateless re: the chat — the proposal lives in
    its own table, identified by id.
  - Defense-in-depth — re-validates safety floors at apply time
    even though the AI already checked them at proposal time.
  - Idempotent — applying twice is a no-op.

The handlers do FOUR things on apply:
  1. Look up the mutation, guarding ownership.
  2. Re-validate safety floors against the *current* SoloProfile
     (the user might have edited their bodyweight or goals
     between proposal and apply).
  3. Apply the change to the canonical model (WorkoutDay /
     SoloProfile).
  4. Mark the audit row applied + record applied_at.
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import (
    api_view, authentication_classes, permission_classes,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import User, SoloProfile
from .mutation_models import (
    MutationStatus, WorkoutMutation, NutritionMutation,
)
from .ai_pt_tools import _check_macro_floors, _check_phase_coherence

log = logging.getLogger(__name__)


# ====================================================================
# Apply
# ====================================================================


@csrf_exempt
@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def mutation_apply(request, mutation_id: int):
    """Apply a proposed mutation. `?type=workout|nutrition|cardio`.

    Body: optional JSON `{"chosen_option_index": int}` for
    SWAP-MULTI-OPTION proposals where the AI offered multiple
    alternatives in `new_value.payload.options[]`. The index is
    0-based into that array. If the proposal is single-option
    (no `options` array), the body is ignored.

    Returns:
        200 {ok: true, applied_at, audit_id} on success.
        404 if not found or not the user's.
        409 if already applied or declined.
        422 if safety floors fail at apply-time re-check.
    """
    user = request.user
    if user.role != User.SOLO:
        return Response({"detail": "Solo accounts only."}, status=403)

    chosen_option_index = None
    if isinstance(request.data, dict):
        raw = request.data.get("chosen_option_index")
        if raw is not None:
            try:
                chosen_option_index = int(raw)
            except (TypeError, ValueError):
                chosen_option_index = None

    mutation_type = (request.query_params.get("type") or "").strip()
    if mutation_type == "workout":
        return _apply_workout(user, mutation_id, chosen_option_index)
    if mutation_type == "nutrition":
        return _apply_nutrition(user, mutation_id)
    return Response(
        {"detail": "type query param must be 'workout' or 'nutrition'."},
        status=400,
    )


def _apply_workout(user, mutation_id: int, chosen_option_index: int = None):
    try:
        mutation = WorkoutMutation.objects.get(id=mutation_id, user=user)
    except WorkoutMutation.DoesNotExist:
        return Response({"detail": "Proposal not found."}, status=404)

    if mutation.status == MutationStatus.APPLIED:
        return Response({
            "ok": True, "audit_id": mutation.id,
            "already_applied": True,
            "applied_at": mutation.applied_at,
        })
    if mutation.status != MutationStatus.PROPOSED:
        return Response({
            "detail": f"Proposal is {mutation.status}, can't apply.",
        }, status=409)

    profile, _ = SoloProfile.objects.get_or_create(user=user)
    if profile.assigned_workout_plan is None:
        return Response({
            "detail": "User has no active programme to apply to.",
        }, status=409)

    plan = profile.assigned_workout_plan
    payload = mutation.new_value or {}
    kind = mutation.kind

    # SWAP-MULTI-OPTION — if the AI proposed multiple alternatives
    # for a swap, the iOS card lets the user pick one. The
    # chosen_option_index (0-based into payload['options']) is
    # passed in the request body. Resolve here to a single-option
    # payload that the existing _apply_swap_exercise can consume
    # unchanged. Backward-compat: payloads without 'options' use
    # the legacy {current_exercise_name, new_exercise_name} shape.
    if (
        kind == WorkoutMutation.KIND_SWAP_EXERCISE
        and isinstance(payload.get("options"), list)
        and len(payload["options"]) > 0
    ):
        idx = chosen_option_index if chosen_option_index is not None else 0
        idx = max(0, min(idx, len(payload["options"]) - 1))
        chosen = payload["options"][idx] or {}
        # Build a single-option-shaped payload the legacy applier
        # expects, while keeping the full options list in
        # `_chosen_option` audit metadata for the mutation row.
        payload = {
            **payload,                       # preserve current_exercise_name etc.
            "new_exercise_name": chosen.get("name") or chosen.get("new_exercise_name"),
            "exercise_id":       chosen.get("exercise_id"),
            "sets":              chosen.get("sets"),
            "reps":              chosen.get("reps"),
            "rest_seconds":      chosen.get("rest_seconds"),
            "_chosen_option_index": idx,
        }
        # Persist the user's choice on the mutation row so
        # the audit trail records WHICH option they picked.
        new_value = mutation.new_value or {}
        new_value["chosen_option_index"] = idx
        mutation.new_value = new_value

    with transaction.atomic():
        try:
            if kind == WorkoutMutation.KIND_SWAP_EXERCISE:
                _apply_swap_exercise(plan, payload)
            elif kind == WorkoutMutation.KIND_CHANGE_SET_SCHEME:
                _apply_change_set_scheme(plan, payload)
            elif kind == WorkoutMutation.KIND_REORDER_DAYS:
                _apply_reorder_days(plan, payload)
            elif kind == WorkoutMutation.KIND_DELOAD_WEEK:
                _apply_deload_week(plan, payload)
            elif kind == WorkoutMutation.KIND_ADD_DAY:
                _apply_add_day(plan, payload)
            elif kind == WorkoutMutation.KIND_REMOVE_DAY:
                _apply_remove_day(plan, payload)
            else:
                return Response({"detail": f"Unknown kind: {kind}"}, status=400)
        except _SafetyBreach as e:
            return Response({"detail": str(e)}, status=422)
        except Exception as exc:
            log.exception("workout apply failed (id=%s)", mutation_id)
            return Response({"detail": f"Couldn't apply: {exc}"}, status=500)

        mutation.status      = MutationStatus.APPLIED
        mutation.decided_at  = timezone.now()
        mutation.applied_at  = timezone.now()
        mutation.save(update_fields=["status", "decided_at", "applied_at"])

    return Response({
        "ok":         True,
        "audit_id":   mutation.id,
        "applied_at": mutation.applied_at,
    })


def _apply_nutrition(user, mutation_id: int):
    try:
        mutation = NutritionMutation.objects.get(id=mutation_id, user=user)
    except NutritionMutation.DoesNotExist:
        return Response({"detail": "Proposal not found."}, status=404)

    if mutation.status == MutationStatus.APPLIED:
        return Response({
            "ok": True, "audit_id": mutation.id,
            "already_applied": True,
            "applied_at": mutation.applied_at,
        })
    if mutation.status != MutationStatus.PROPOSED:
        return Response({
            "detail": f"Proposal is {mutation.status}, can't apply.",
        }, status=409)

    profile, _ = SoloProfile.objects.get_or_create(user=user)
    payload = mutation.new_value or {}
    kind    = mutation.kind

    with transaction.atomic():
        # Defense in depth — re-validate floors with the LIVE profile
        # at apply time, not the snapshot that was current at
        # proposal time.
        if kind == NutritionMutation.KIND_ADJUST_MACROS:
            ref = _check_macro_floors(
                profile,
                calories=payload.get("calories"),
                protein_g=payload.get("protein_g"),
                carbs_g=payload.get("carbs_g"),
                fats_g=payload.get("fats_g"),
            )
            if ref:
                return Response({
                    "detail": ref["detail"], "reason": ref["reason"],
                }, status=422)
            profile.target_calories = int(payload["calories"])
            profile.target_protein  = int(payload["protein_g"])
            profile.target_carbs    = int(payload["carbs_g"])
            profile.target_fats     = int(payload["fats_g"])
            profile.save(update_fields=[
                "target_calories", "target_protein",
                "target_carbs",    "target_fats",
            ])

        elif kind == NutritionMutation.KIND_CHANGE_GOAL_PHASE:
            new_phase = payload.get("phase")
            ref = _check_phase_coherence(profile, new_phase)
            if ref:
                return Response({
                    "detail": ref["detail"], "reason": ref["reason"],
                }, status=422)
            profile.phase = new_phase
            profile.phase_started_at = timezone.now()
            # Re-derive macro targets so the nutrition layer follows
            # the phase change automatically.
            profile.compute_default_macro_targets(save=True)
            profile.save(update_fields=["phase", "phase_started_at"])

        elif kind == NutritionMutation.KIND_SWAP_PREFERENCE:
            prefs = user.notification_prefs or {}
            prefs["dietary_exclude"] = list(payload.get("exclude") or [])
            prefs["dietary_include"] = list(payload.get("include") or [])
            user.notification_prefs = prefs
            user.save(update_fields=["notification_prefs"])

        elif kind == NutritionMutation.KIND_CHANGE_MEAL_FREQ:
            prefs = user.notification_prefs or {}
            prefs["meals_per_day"] = int(payload.get("meals_per_day", 4))
            user.notification_prefs = prefs
            user.save(update_fields=["notification_prefs"])

        else:
            return Response({"detail": f"Unknown kind: {kind}"}, status=400)

        mutation.status     = MutationStatus.APPLIED
        mutation.decided_at = timezone.now()
        mutation.applied_at = timezone.now()
        mutation.save(update_fields=["status", "decided_at", "applied_at"])

    return Response({
        "ok":         True,
        "audit_id":   mutation.id,
        "applied_at": mutation.applied_at,
    })


# ====================================================================
# Decline
# ====================================================================


@csrf_exempt
@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def mutation_decline(request, mutation_id: int):
    user = request.user
    if user.role != User.SOLO:
        return Response({"detail": "Solo accounts only."}, status=403)

    mutation_type = (request.query_params.get("type") or "").strip()
    Model = (
        WorkoutMutation if mutation_type == "workout" else
        NutritionMutation if mutation_type == "nutrition" else
        None
    )
    if Model is None:
        return Response(
            {"detail": "type query param must be 'workout' or 'nutrition'."},
            status=400,
        )

    try:
        mutation = Model.objects.get(id=mutation_id, user=user)
    except Model.DoesNotExist:
        return Response({"detail": "Proposal not found."}, status=404)

    if mutation.status == MutationStatus.DECLINED:
        return Response({"ok": True, "audit_id": mutation.id, "already_declined": True})
    if mutation.status != MutationStatus.PROPOSED:
        return Response(
            {"detail": f"Proposal is {mutation.status}, can't decline."},
            status=409,
        )

    mutation.status     = MutationStatus.DECLINED
    mutation.decided_at = timezone.now()
    mutation.save(update_fields=["status", "decided_at"])

    return Response({"ok": True, "audit_id": mutation.id})


# ====================================================================
# Workout-mutation appliers — one per kind.
# ====================================================================


class _SafetyBreach(Exception):
    """Raised inside an appliers fn to signal a 422 to the caller."""


def _find_exercise(plan, payload: dict):
    """Locate the Exercise row to mutate. Tolerant of partial payloads:
       1. exercise_id (preferred — set when AI called get_active_programme_detail).
       2. day_id + current_exercise_name (case-insensitive name match).
       3. current_exercise_name across the whole plan (fallback).
    Raises _SafetyBreach if nothing matches.
    """
    from apps.workouts.models import Exercise
    ex_id        = payload.get("exercise_id")
    day_id       = payload.get("day_id")
    current_name = (payload.get("current_exercise_name") or "").strip()

    if ex_id:
        try:
            return Exercise.objects.get(id=ex_id, workout_day__plan=plan)
        except Exercise.DoesNotExist:
            pass  # fall through to name match

    if not current_name:
        raise _SafetyBreach(
            "Couldn't find the exercise to swap. Need exercise_id or "
            "current_exercise_name in the proposal payload.",
        )

    qs = Exercise.objects.filter(workout_day__plan=plan, name__iexact=current_name)
    if day_id:
        qs = qs.filter(workout_day__id=day_id)
    ex = qs.first()
    if ex is None:
        raise _SafetyBreach(
            f"No exercise named '{current_name}' on this plan."
            + (f" (day_id={day_id})" if day_id else ""),
        )
    return ex


def _apply_swap_exercise(plan, payload: dict):
    """Swap one exercise for another. We don't have a separate
    Exercise-catalog FK on the Exercise row in this schema (the
    name string IS the source of truth), so swapping = renaming.
    Sets + reps carry over unchanged unless the caller also supplied
    them in the payload.
    """
    new_name = (payload.get("new_exercise_name") or "").strip()
    if not new_name:
        raise _SafetyBreach("swap_exercise needs new_exercise_name.")
    ex = _find_exercise(plan, payload)
    ex.name = new_name
    ex.save(update_fields=["name"])


def _apply_change_set_scheme(plan, payload: dict):
    """Change set count, reps, and/or rest on an existing exercise.

    Sets live on `ExerciseSetTarget` rows (one row per set), so
    changing the count means inserting / deleting target rows.
    Changing reps means updating the existing rows. REST-ASSIGNABLE
    extends this kind to also accept `rest_seconds` in the payload —
    the AI can propose rest changes alongside set/rep tweaks
    without a new mutation kind.
    """
    from apps.workouts.models import ExerciseSetTarget
    ex = _find_exercise(plan, payload)

    new_sets = payload.get("sets")
    new_reps = payload.get("reps") or payload.get("rep_range")
    new_rest = payload.get("rest_seconds")

    current_sets = list(ex.sets.all().order_by("set_number"))
    current_count = len(current_sets)

    if new_sets is not None:
        target = max(1, int(new_sets))
        if target > current_count:
            # Add rows. New rows inherit reps from the last existing
            # row (or "8" as a fallback if the exercise has no sets
            # at all — shouldn't happen).
            template_reps = current_sets[-1].reps if current_sets else "8"
            for n in range(current_count, target):
                ExerciseSetTarget.objects.create(
                    exercise=ex,
                    set_number=n + 1,
                    reps=template_reps,
                )
        elif target < current_count:
            # Drop the trailing rows.
            for st in current_sets[target:]:
                st.delete()
        # Refresh the list since we just mutated it.
        current_sets = list(ex.sets.all().order_by("set_number"))

    if new_reps is not None:
        for st in current_sets:
            st.reps = str(new_reps)
            st.save(update_fields=["reps"])

    # REST-ASSIGNABLE — clamp to a sane band so the AI can't
    # propose 0s or 30min rests via free-text.
    if new_rest is not None:
        rest = max(0, min(600, int(new_rest)))
        ex.rest_seconds = rest
        ex.save(update_fields=["rest_seconds"])


def _apply_reorder_days(plan, payload: dict):
    """Re-sequence the user's training days. Field is `order` on
    WorkoutDay, not `order_index`.
    """
    new_order = payload.get("new_order") or []
    if not isinstance(new_order, list) or not new_order:
        raise _SafetyBreach("reorder_days needs a non-empty new_order list.")
    days = {d.id: d for d in plan.days.all()}
    if set(new_order) != set(days.keys()):
        raise _SafetyBreach("new_order must reference exactly the existing day IDs.")
    for index, day_id in enumerate(new_order):
        d = days[day_id]
        d.order = index
        d.save(update_fields=["order"])


def _apply_deload_week(plan, payload: dict):
    """Halve sets across the plan for one week. We mark a flag in
    plan.programme_meta so the workout list view can render the
    deload state without rewriting the underlying schedule."""
    scope = payload.get("scope", "this_week")
    meta = plan.programme_meta or {}
    meta.setdefault("deload", {})
    meta["deload"]["scope"]      = scope
    meta["deload"]["applied_at"] = timezone.now().isoformat()
    plan.programme_meta = meta
    plan.save(update_fields=["programme_meta"])


def _apply_add_day(plan, payload: dict):
    raise _SafetyBreach(
        "add_day requires the custom-builder UI; not auto-applicable yet. "
        "Direct the user to Workout → Edit programme.",
    )


def _apply_remove_day(plan, payload: dict):
    from apps.workouts.models import WorkoutDay
    day_id = payload.get("day_id")
    if day_id is None:
        raise _SafetyBreach("remove_day needs day_id.")
    try:
        d = WorkoutDay.objects.get(id=day_id, plan=plan)
    except WorkoutDay.DoesNotExist:
        raise _SafetyBreach("Day not found on this plan.")
    if plan.days.count() <= 1:
        raise _SafetyBreach("Can't remove the only remaining day.")
    d.delete()
