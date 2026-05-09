"""T2.3 — Exercise candidate filter for AI workout build.

Pre-filters the ~1500-row ExerciseCatalog into a ~200-row shortlist
the AI workout build (Phase 1 catalog grounding, T3.1) can inject
into Claude's system prompt as the "pick from these" candidate set.

Hard filter:
    • equipment compatibility (full_gym / home_with_weights /
      bodyweight_only / mixed → set of acceptable `equipment` tags)
    • level (beginner profiles never see expert-tier rows)
    • avoidances (free-text strings like "knee pain" / "no overhead
      press" → name + secondary-muscle exclusion check)

Soft rank (lower = better, sort ascending):
    • muscle priority (goal-aligned muscles bubble up)
    • compound > isolation for compound-focused goals
    • prefer rows with animation_url + form_description populated
      (iOS render fidelity)

The recommendation: send ~200 candidates per call. Claude can
still hallucinate names; the AI build view validates every
returned `exercise_catalog_id` against this slice and retries on
miss.
"""
from __future__ import annotations

import re
from typing import Iterable

from .models import ExerciseCatalog


# ----------------------------------------------------------------
# Equipment buckets — onboarding answer → set of catalog tags
# ----------------------------------------------------------------
EQUIPMENT_TAGS: dict[str, set[str]] = {
    "full_gym": {
        "barbell", "dumbbell", "machine", "cable", "kettlebells",
        "ez_curl_bar", "bands", "body_only", "other",
        "medicine ball", "exercise ball", "foam roll",
    },
    "home_with_weights": {
        "dumbbell", "kettlebells", "bands", "body_only",
        "medicine ball", "exercise ball", "foam roll",
    },
    "bodyweight_only": {
        "body_only", "bands",
    },
    "mixed": {
        "body_only", "dumbbell", "bands", "kettlebells",
        "medicine ball", "exercise ball",
    },
}


# ----------------------------------------------------------------
# Goal → primary muscles emphasis. Each goal weights certain muscle
# groups higher in the rank score so AI builds cover the user's
# actual focus instead of returning a generic split.
# ----------------------------------------------------------------
GOAL_MUSCLE_PRIORITIES: dict[str, list[str]] = {
    "lose_fat": [
        "quadriceps", "glutes", "hamstrings", "back", "chest",
        "shoulders",  # high-volume compound bias
    ],
    "build_muscle": [
        "chest", "back", "quadriceps", "glutes", "hamstrings",
        "shoulders", "biceps", "triceps", "calves",
    ],
    "get_stronger": [
        "quadriceps", "glutes", "hamstrings", "back", "chest",
        "shoulders",  # squat / deadlift / bench / OHP groups
    ],
    "stay_consistent": [
        "chest", "back", "quadriceps", "glutes", "shoulders",
    ],
    "train_for_sport": [
        "quadriceps", "glutes", "hamstrings", "calves", "abdominals",
        "back",
    ],
}


def _normalised_avoidance_tokens(avoidances: Iterable[str]) -> list[str]:
    """Lowercase + strip + drop tiny words from the avoidance array.

    Used for substring matching against `name` + `secondary_muscles`.
    """
    out: list[str] = []
    for raw in (avoidances or []):
        s = (raw or "").strip().lower()
        if not s:
            continue
        # Drop "no " / "avoid " prefixes — "no overhead press" → "overhead press"
        s = re.sub(r"^(no |avoid |skip )", "", s)
        # Drop body-part-only tokens shorter than 4 chars
        if len(s) < 4:
            continue
        out.append(s)
    return out


def _allowed_levels(profile_level: str) -> set[str]:
    """Beginner profiles never see expert. Intermediates see beginner +
    intermediate. Experts see all."""
    pl = (profile_level or "").lower()
    if pl == ExerciseCatalog.LEVEL_BEGINNER:
        return {ExerciseCatalog.LEVEL_BEGINNER, ExerciseCatalog.LEVEL_INTERMEDIATE, ""}
    if pl == ExerciseCatalog.LEVEL_INTERMEDIATE:
        return {
            ExerciseCatalog.LEVEL_BEGINNER, ExerciseCatalog.LEVEL_INTERMEDIATE,
            ExerciseCatalog.LEVEL_EXPERT, "",
        }
    # Experts and unset users see everything.
    return {
        ExerciseCatalog.LEVEL_BEGINNER, ExerciseCatalog.LEVEL_INTERMEDIATE,
        ExerciseCatalog.LEVEL_EXPERT, "",
    }


def candidate_exercises(profile, *, max_n: int = 200) -> list[dict]:
    """Return the top-N ExerciseCatalog rows ranked for this user.

    Args:
        profile: a SoloProfile (or None — falls back to no-filter)
        max_n:   cap on returned rows

    Returns:
        list of dicts ready to JSON-encode into a Claude prompt:
            [{"id", "name", "equipment", "primary_muscle",
              "level", "mechanic"}, ...]
    """
    qs = ExerciseCatalog.objects.filter(is_published=True)

    # Hard filters
    if profile is not None:
        equip_key = (getattr(profile, "equipment", "") or "").lower()
        allowed_equip = EQUIPMENT_TAGS.get(equip_key)
        if allowed_equip:
            qs = qs.filter(equipment__in=list(allowed_equip) + [""])

        levels = _allowed_levels(getattr(profile, "experience", ""))
        qs = qs.filter(level__in=list(levels))

    rows = list(qs.only(
        "id", "name", "equipment", "muscle_group", "secondary_muscles",
        "level", "mechanic", "form_description", "animation_url",
    ))

    # Avoidances — substring match on name + secondary_muscles.
    avoid_tokens: list[str] = []
    if profile is not None:
        avoid_tokens = _normalised_avoidance_tokens(
            getattr(profile, "avoidances", None) or [],
        )
    if avoid_tokens:
        def _is_avoided(r: ExerciseCatalog) -> bool:
            blob = (r.name + " " + r.secondary_muscles).lower()
            return any(tok in blob for tok in avoid_tokens)
        rows = [r for r in rows if not _is_avoided(r)]

    # Soft rank
    goals: list[str] = []
    if profile is not None:
        goals = list(getattr(profile, "goals", None) or [])
    priority: list[str] = []
    for g in goals:
        priority.extend(GOAL_MUSCLE_PRIORITIES.get(g, []))

    def _score(r: ExerciseCatalog) -> tuple[int, int, int, str]:
        # Lower is better.
        muscle_idx = (
            priority.index(r.muscle_group)
            if r.muscle_group in priority
            else 999
        )
        # Compound preferred for compound-focused goals.
        wants_compound = any(
            g in goals for g in ("get_stronger", "build_muscle", "lose_fat")
        )
        compound_score = (
            0 if (wants_compound and r.mechanic == ExerciseCatalog.MECHANIC_COMPOUND)
            else 1
        )
        # Prefer rows with rendering assets so iOS shows a real animation.
        render_score = (
            0 if (r.animation_url or r.form_description) else 1
        )
        return (muscle_idx, compound_score, render_score, r.name.lower())

    rows.sort(key=_score)
    sliced = rows[:max_n]

    return [
        {
            "id":             r.id,
            "name":           r.name,
            "equipment":      r.equipment or "",
            "primary_muscle": r.muscle_group or "",
            "level":          r.level or "",
            "mechanic":       r.mechanic or "",
        }
        for r in sliced
    ]
