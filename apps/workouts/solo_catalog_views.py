"""
SOLO-02 — Public programmes catalog.

Two endpoints:

  • GET  /api/workouts/solo/programmes/        — list catalog
        Query params: ?goal=&experience=&equipment=&days=
        All optional; returns the full catalog if none provided.
        Filtering is done in Python (the catalog is small enough
        that joining + indexing is overkill); revisit when we
        cross ~200 programmes.

  • POST /api/workouts/solo/programmes/<id>/assign/
        Deep-clones the catalog template into a per-user WorkoutPlan,
        sets SoloProfile.assigned_workout_plan, returns the fresh
        plan id. Idempotent — re-assigning replaces the previous
        clone (we don't want stale plans piling up).

Catalog rows are seeded by the `seed_solo_programmes` management
command. They live as `WorkoutPlan.is_solo_template=True` rows
authored by a system user — the same shape as a trainer-authored
plan, just publicly readable.
"""
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.models import User, SoloProfile

from .models import (
    WorkoutPlan, WorkoutDay, Exercise, ExerciseSetTarget,
)
from .solo_catalog_ranker import rank_programmes


# --------------------------------------------------------------------
# Filter helpers
# --------------------------------------------------------------------
def _matches(meta: dict, *, goal: str | None, experience: str | None,
             equipment: str | None, days: int | None) -> bool:
    """True iff the programme's `programme_meta` is compatible with the
    requested filter. Any unmatched filter immediately disqualifies."""
    if goal:
        plan_goals = set(meta.get("goals") or [])
        if goal not in plan_goals:
            return False
    if experience:
        # Programmes can declare a specific experience level OR be
        # open to anyone (`"any"`). The latter survives every filter.
        plan_exp = meta.get("experience") or ""
        if plan_exp not in (experience, "any", ""):
            return False
    if equipment:
        plan_eq = meta.get("equipment") or ""
        if plan_eq not in (equipment, "any", ""):
            return False
    if days:
        # Allow ±1 day tolerance — a 4-day programme is a fine fit
        # for a user who said 3 or 5.
        plan_days = meta.get("days_per_week") or 0
        if plan_days and abs(plan_days - days) > 1:
            return False
    return True


def _serialize_card(plan: WorkoutPlan) -> dict:
    """Lightweight payload for a catalog card. We don't ship the full
    days/exercises tree — the catalog only needs the meta + name.

    The `evidence` + `source_attribution` + `recommended_for` fields
    are exactly what powers the "Why this programme?" disclosure on
    the iOS card, and what AI PT (E.2) reads when explaining a
    recommendation. Carrying them in the list payload (vs. requiring
    a second per-card request) keeps the card UX instant — no
    spinner the moment you tap "Why".
    """
    meta = plan.programme_meta or {}
    return {
        "id":             plan.id,
        "name":           plan.name,
        "tagline":        meta.get("tagline", ""),
        "summary":        meta.get("summary", ""),
        "goals":          meta.get("goals") or [],
        "experience":     meta.get("experience", ""),
        "equipment":      meta.get("equipment", ""),
        "days_per_week":  meta.get("days_per_week", 0),
        "weeks":          meta.get("weeks", 0),
        # Research-backing payload (SOLO-02 v2)
        "evidence":           meta.get("evidence") or [],
        "source_attribution": meta.get("source_attribution", ""),
        "recommended_for":    meta.get("recommended_for") or [],
        "not_recommended_for": meta.get("not_recommended_for") or [],
        "progression_rule":   meta.get("progression_rule", ""),
        "deload_strategy":    meta.get("deload_strategy", ""),
        "weekly_volume_per_muscle": meta.get("weekly_volume_per_muscle") or {},
    }


# --------------------------------------------------------------------
# List
# --------------------------------------------------------------------
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def solo_programmes_list(request):
    """List public programme templates, optionally filtered.

    CATALOG-PERSONALISED-TOP3 (#131) — the response now includes
    a `recommended` array (top-3 ranked by the user's profile)
    in addition to the existing `programmes` array (the full
    filtered list). iOS renders the `recommended` cards above
    the alphabetised list; users can still scroll the full list
    below.

    The ranking is rule-based + transparent (see
    `solo_catalog_ranker.py`). Each recommended card carries a
    `match_reasons` list so the UI can show "Why this programme?"
    inline without a second request.
    """
    goal       = request.query_params.get("goal") or None
    experience = request.query_params.get("experience") or None
    equipment  = request.query_params.get("equipment") or None
    try:
        days = int(request.query_params.get("days") or 0) or None
    except (TypeError, ValueError):
        days = None

    qs = (
        WorkoutPlan.objects
        .filter(is_solo_template=True, is_active=True)
        .order_by("name")
    )
    matched_plans = [
        p for p in qs
        if _matches(p.programme_meta or {}, goal=goal,
                    experience=experience, equipment=equipment, days=days)
    ]

    # Build the user's profile inputs for scoring. If the request
    # came with explicit query params, prefer those (the user is
    # actively filtering); otherwise read from SoloProfile so the
    # default unfiltered list still surfaces personal top-3.
    user = request.user
    profile = getattr(user, "solo_profile", None)
    profile_inputs = {
        "goals":         [goal] if goal else (profile.goals if profile else []),
        "experience":    experience or (profile.experience if profile else ""),
        "equipment":     equipment or (profile.equipment if profile else ""),
        "days_per_week": days or (profile.days_per_week if profile else 0),
    }

    recommended_tuples, others_tuples = rank_programmes(
        matched_plans, profile_inputs, top_n=3,
    )

    def _serialise_with_reasons(plan, score, reasons):
        card = _serialize_card(plan)
        card["match_score"] = score
        card["match_reasons"] = reasons
        return card

    recommended = [
        _serialise_with_reasons(p, s, r) for (p, s, r) in recommended_tuples
    ]
    # Preserve alphabetical order in the rest by re-sorting on name.
    others_plans = [t[0] for t in others_tuples]
    others_plans.sort(key=lambda p: (p.name or "").lower())
    others = [_serialize_card(p) for p in others_plans]

    return Response({
        "recommended": recommended,
        "programmes":  recommended + others,  # back-compat: full list
    })


# --------------------------------------------------------------------
# Assign
# --------------------------------------------------------------------
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def solo_programmes_assign(request, programme_id):
    """Clone the catalog template into a per-user WorkoutPlan."""
    user = request.user
    if user.role != User.SOLO:
        return Response(
            {"detail": "Only Solo accounts can self-assign programmes."},
            status=status.HTTP_403_FORBIDDEN,
        )

    template = get_object_or_404(
        WorkoutPlan, id=programme_id, is_solo_template=True,
    )
    profile, _ = SoloProfile.objects.get_or_create(user=user)

    with transaction.atomic():
        # If the user previously assigned a programme, clear it. Old
        # plans linger in the DB so historical sessions still link to
        # them, but they're no longer the active plan.
        previous = profile.assigned_workout_plan
        if previous is not None:
            previous.is_active = False
            previous.save(update_fields=["is_active"])

        # Deep-clone: WorkoutPlan → WorkoutDay → Exercise → ExerciseSetTarget.
        clone = WorkoutPlan.objects.create(
            user=user,
            name=template.name,
            is_active=True,
            is_template=False,
            is_solo_template=False,
            source_template=template,
            programme_meta=template.programme_meta,
        )
        for src_day in template.days.all().prefetch_related("exercises__sets"):
            new_day = WorkoutDay.objects.create(
                plan=clone, title=src_day.title, order=src_day.order,
            )
            for src_ex in src_day.exercises.all():
                new_ex = Exercise.objects.create(
                    workout_day=new_day,
                    name=src_ex.name,
                    label=src_ex.label,
                    order=src_ex.order,
                    superset_group=src_ex.superset_group,
                )
                for src_set in src_ex.sets.all():
                    ExerciseSetTarget.objects.create(
                        exercise=new_ex,
                        set_number=src_set.set_number,
                        reps=src_set.reps,
                    )

        profile.assigned_workout_plan = clone
        profile.save(update_fields=["assigned_workout_plan"])

    return Response({"ok": True, "plan_id": clone.id, "plan_name": clone.name})


# --------------------------------------------------------------------
# Custom programme creation (user-authored)
# --------------------------------------------------------------------
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def solo_programmes_create_custom(request):
    """POST /api/workouts/solo/programmes/custom/

    Build-your-own programme. Solo users can author a workout from
    scratch when none of the catalog templates fit. Body shape:

        {
            "name": "My PPL variant",
            "days": [
                {"title": "Push", "exercises": [
                    {"name": "Bench Press", "label": "A",
                     "sets": [{"set_number": 1, "reps": "8"}, ...]},
                    ...
                ]},
                ...
            ]
        }

    Created plans are auto-assigned (replacing any prior assignment),
    same as catalog templates. Authored plans live alongside catalog
    clones in the same WorkoutPlan table — distinguished by
    `source_template=NULL`.

    AI PT (E.2) will eventually be able to GENERATE one of these from
    a natural-language brief — same shape, different origin.
    """
    user = request.user
    if user.role != User.SOLO:
        return Response(
            {"detail": "Custom programme creation is for Solo accounts only."},
            status=status.HTTP_403_FORBIDDEN,
        )

    data = request.data or {}
    name = (data.get("name") or "").strip()[:255]
    days = data.get("days") or []
    if not name:
        return Response({"detail": "Programme name is required."}, status=400)
    if not isinstance(days, list) or not days:
        return Response({"detail": "At least one workout day is required."}, status=400)

    profile, _ = SoloProfile.objects.get_or_create(user=user)

    with transaction.atomic():
        if profile.assigned_workout_plan is not None:
            profile.assigned_workout_plan.is_active = False
            profile.assigned_workout_plan.save(update_fields=["is_active"])

        plan = WorkoutPlan.objects.create(
            user=user, name=name,
            is_active=True, is_template=False, is_solo_template=False,
            programme_meta={"source_attribution": "User-authored"},
        )
        for d_idx, day_spec in enumerate(days):
            day = WorkoutDay.objects.create(
                plan=plan,
                title=str(day_spec.get("title", f"Day {d_idx + 1}"))[:100],
                order=d_idx,
            )
            for e_idx, ex_spec in enumerate(day_spec.get("exercises") or []):
                ex = Exercise.objects.create(
                    workout_day=day,
                    name=str(ex_spec.get("name", "Exercise"))[:255],
                    label=str(ex_spec.get("label", chr(65 + e_idx)))[:10],
                    order=e_idx,
                )
                for s_idx, s in enumerate(ex_spec.get("sets") or []):
                    ExerciseSetTarget.objects.create(
                        exercise=ex,
                        set_number=int(s.get("set_number", s_idx + 1)),
                        reps=str(s.get("reps", "8-12"))[:20],
                    )

        profile.assigned_workout_plan = plan
        profile.save(update_fields=["assigned_workout_plan"])

    return Response(
        {"ok": True, "plan_id": plan.id, "plan_name": plan.name},
        status=status.HTTP_201_CREATED,
    )
