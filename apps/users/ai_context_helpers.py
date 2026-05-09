"""T2.1 / T2.2 — Calculated AI context helpers.

Pure-read functions that compute summary signals from existing
models for the AI PT user-context block. Kept separate from
`ai_pt_views._build_user_context` so they're individually
testable + reusable by the weekly review surface (#228) without
copy-paste.

All helpers are best-effort: if the underlying data isn't there,
they return None and the context line is omitted. Never raise.
"""
from datetime import timedelta

from django.utils import timezone


# --------------------------------------------------------------------
# T2.1 — Body trajectory + adherence
# --------------------------------------------------------------------
def recent_weight_slope_kg_per_week(user, *, days: int = 28) -> float | None:
    """Linear-style slope over the last N days of bodyweight check-ins.

    Returns kg/week (positive = gaining, negative = losing). None if
    fewer than 2 logs in the window — a single point can't define a
    slope. Same windowing logic as `_build_user_context`'s 4-week
    section but exposed standalone so the weekly review can read it.
    """
    try:
        from apps.progress.models import SoloBodyweightLog
    except Exception:
        return None
    cutoff = timezone.localdate() - timedelta(days=days)
    logs = list(
        SoloBodyweightLog.objects
        .filter(user=user, logged_on__gte=cutoff)
        .order_by("logged_on")
    )
    if len(logs) < 2:
        return None
    span_days = max((logs[-1].logged_on - logs[0].logged_on).days, 1)
    delta_kg = logs[-1].kg - logs[0].kg
    return round(delta_kg / span_days * 7, 2)


def food_adherence_14d(user) -> dict | None:
    """% of last 14 days where the user hit their protein target,
    plus average kcal vs target.

    Returns:
        {
          "days_with_log":     int,   # 0–14
          "days_protein_hit":  int,   # subset where protein >= 0.9 × target
          "avg_kcal_vs_target": float, # +/- ratio (e.g. 0.95 = 5% under)
        }
        or None if no SoloProfile / no targets / no log entries.
    """
    try:
        from apps.nutrition.models import SoloFoodLogEntry
        from apps.users.models import SoloProfile
    except Exception:
        return None
    profile = SoloProfile.objects.filter(user=user).first()
    if profile is None or not (profile.target_calories or 0) > 0:
        return None

    today = timezone.localdate()
    start = today - timedelta(days=14)
    rows = list(
        SoloFoodLogEntry.objects
        .filter(user=user, consumed_on__gte=start, consumed_on__lt=today)
    )
    if not rows:
        return None

    by_day: dict = {}
    for r in rows:
        d = r.consumed_on
        bucket = by_day.setdefault(d, {"kcal": 0.0, "protein": 0.0})
        bucket["kcal"]    += r.calories
        bucket["protein"] += r.protein

    target_kcal = profile.target_calories
    target_protein = max(1, profile.target_protein)

    days_with_log = len(by_day)
    days_protein_hit = sum(
        1 for b in by_day.values() if b["protein"] >= 0.9 * target_protein
    )
    avg_ratio = (
        sum(b["kcal"] / target_kcal for b in by_day.values()) / days_with_log
    )
    return {
        "days_with_log":      days_with_log,
        "days_protein_hit":   days_protein_hit,
        "avg_kcal_vs_target": round(avg_ratio, 2),
    }


def workout_completion_14d(user) -> dict | None:
    """% of scheduled training-day slots over the last 14 days that had
    a logged session.

    "Scheduled" = `SoloProfile.training_days` array. If unset, falls
    back to a simple count of completed sessions in the window
    against the user's `days_per_week` × 2 weeks.

    Returns:
        {
          "scheduled":   int,   # expected sessions in the 14 day window
          "completed":   int,   # completed sessions
          "ratio":       float, # 0.0–1.0+
        }
        or None on error.
    """
    try:
        from apps.workouts.models import WorkoutSession
        from apps.users.models import SoloProfile
    except Exception:
        return None
    profile = SoloProfile.objects.filter(user=user).first()
    if profile is None:
        return None

    cutoff = timezone.now() - timedelta(days=14)
    completed = WorkoutSession.objects.filter(
        user=user, is_complete=True, completed_at__gte=cutoff,
    ).count()

    days_arr = list(profile.training_days or [])
    if days_arr:
        scheduled = len(days_arr) * 2
    else:
        scheduled = max(1, (profile.days_per_week or 3) * 2)
    ratio = round(completed / scheduled, 2) if scheduled else 0.0
    return {
        "scheduled": scheduled,
        "completed": completed,
        "ratio":     ratio,
    }


# --------------------------------------------------------------------
# T2.2 — Cross-domain summaries
# --------------------------------------------------------------------
def active_workout_summary(user) -> str | None:
    """Compact one-line summary of the user's active workout plan,
    safe to splice into the AI nutrition build prompt so the model
    can reason about training volume + frequency when picking macros.

    Format example:
        "PPL split, 5 days/wk, 24 working sets/wk legs, 18 push, 16 pull"
    """
    try:
        from apps.users.models import SoloProfile
    except Exception:
        return None
    profile = SoloProfile.objects.filter(user=user).first()
    plan = getattr(profile, "assigned_workout_plan", None)
    if plan is None:
        return None

    meta = plan.programme_meta or {}
    parts = [plan.name]
    if meta.get("days_per_week"):
        parts.append(f"{meta['days_per_week']} days/wk")
    if meta.get("weeks"):
        parts.append(f"{meta['weeks']} wk")

    # Volume per primary muscle group (count working sets across all
    # days). Cheap aggregate; doesn't sub for a real volume tracker
    # but enough for AI to know "this user trains legs hard."
    try:
        from apps.workouts.models import Exercise, WorkoutDay
        days = WorkoutDay.objects.filter(plan=plan)
        ex = Exercise.objects.filter(workout_day__in=days).select_related("catalog_item")
        muscle_set_counts: dict[str, int] = {}
        for e in ex:
            mus = (
                getattr(e.catalog_item, "primary_muscle", None)
                or "other"
            )
            n_sets = e.sets.count() if hasattr(e, "sets") else 0
            muscle_set_counts[mus] = muscle_set_counts.get(mus, 0) + n_sets
        if muscle_set_counts:
            top = sorted(muscle_set_counts.items(), key=lambda x: -x[1])[:3]
            volume_hint = ", ".join(f"{n} {m}" for m, n in top)
            parts.append(f"sets/wk: {volume_hint}")
    except Exception:
        pass

    return " — ".join(parts)


def active_nutrition_summary(user) -> str | None:
    """Compact one-line summary of the user's active nutrition plan,
    safe to splice into the AI workout build prompt so the model
    can reason about kcal availability when prescribing volume.

    Format example:
        "2200 kcal target / 165p / 240c / 70f, last 14d adherence: 11/14 protein hit, 0.94× kcal"
    """
    try:
        from apps.users.models import SoloProfile
    except Exception:
        return None
    profile = SoloProfile.objects.filter(user=user).first()
    if profile is None or not (profile.target_calories or 0) > 0:
        return None
    parts = [
        f"{profile.target_calories} kcal target",
        f"{profile.target_protein}p / {profile.target_carbs}c / {profile.target_fats}f",
    ]
    adh = food_adherence_14d(user)
    if adh:
        parts.append(
            f"last 14d: {adh['days_protein_hit']}/{adh['days_with_log']} protein hit, "
            f"{adh['avg_kcal_vs_target']}× kcal"
        )
    return ", ".join(parts)


# --------------------------------------------------------------------
# T2.10 — Recent user-edits summary (used here for cross-domain
# awareness; the RecentEditLog model itself is added in a later
# commit and this helper falls back to provenance flags if the log
# isn't populated yet.)
# --------------------------------------------------------------------
def recent_user_edits_summary(user, *, n: int = 10) -> str | None:
    """Natural-language summary of recent user-side edits. Reads
    from RecentEditLog when available, falls back to scanning
    recent Exercise rows tagged `provenance=user_edit`.

    Returns None if there's nothing meaningful to surface.
    """
    try:
        from apps.users.models import RecentEditLog
    except Exception:
        RecentEditLog = None

    rows: list[str] = []

    if RecentEditLog is not None:
        try:
            qs = (RecentEditLog.objects
                  .filter(user=user)
                  .order_by("-created_at")[:n])
            for row in qs:
                summary = (row.summary or "").strip()
                if summary:
                    rows.append(summary)
        except Exception:
            pass

    # Fallback — recent Exercise rows on the active plan that the
    # user touched directly. We can't know exactly when they edited
    # without a log, but we can confirm the *fact* of user edits
    # exists, which is enough for the AI to ask in chat.
    if not rows:
        try:
            from apps.workouts.models import Exercise
            from apps.users.models import SoloProfile
            profile = SoloProfile.objects.filter(user=user).first()
            plan = getattr(profile, "assigned_workout_plan", None)
            if plan is not None:
                user_edits = Exercise.objects.filter(
                    workout_day__plan=plan,
                    provenance="user_edit",
                ).count()
                if user_edits:
                    rows.append(
                        f"{user_edits} exercise(s) on this plan show "
                        f"manual edits since AI build"
                    )
        except Exception:
            pass

    if not rows:
        return None
    return "; ".join(rows)
