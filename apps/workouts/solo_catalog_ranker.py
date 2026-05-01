"""
CATALOG-PERSONALISED-TOP3 (#131) — transparent rule-based ranking
for the public programme catalog.

Goal: surface the three programmes most likely to fit the user
at the top of `/api/workouts/solo/programmes/` so the
"recommended" tab on iOS lands on real recommendations, not
alphabetical order.

Design choices:

  • Transparent rules + documented weights, NOT a black-box ML
    model. Each score component can be explained to the user
    ("Why this programme?") without inference latency or
    training data.
  • Pure functions; no Django models. The catalog views call
    `score_programme(meta, profile_inputs)` and sort.
  • Weights are tunable from one place (`WEIGHTS` dict at top
    of file). Future tuning lives in DECISIONS.md, not in
    scattered magic numbers.

Score components (max 100 per programme):

  1. Goal overlap            (40 pts) — direct goal match is the
                                       strongest signal.
  2. Experience match        (20 pts) — beginner ≠ advanced
                                       programme is the most
                                       common mis-fit.
  3. Equipment match         (20 pts) — running a barbell-only
                                       programme on bodyweight
                                       equipment is broken.
  4. Days/week match         (15 pts) — within ±1 day is fine,
                                       further drops fast.
  5. recommended_for tag bonus (5 pts)
  6. not_recommended_for tag malus (-25 pts) — a hard penalty
                                       for known mis-fits (e.g.
                                       beginner-flagged programme
                                       for an advanced lifter).

Returns a tuple (score, reasons) so the iOS layer can show
"Why this programme?" without a second request:

  (78, ["Matches your goal: build muscle",
        "Matches your experience: intermediate",
        "Matches your equipment: home with weights",
        "Matches your days/week: 4"])
"""
from __future__ import annotations

from typing import Optional


# ====================================================================
# Tunable weights — adjust here, document changes in DECISIONS.md.
# ====================================================================

WEIGHTS = {
    "goal":        40,
    "experience":  20,
    "equipment":   20,
    "days":        15,
    "recommended_for_bonus": 5,
    "not_recommended_for_penalty": -25,
}


# Days/week tolerance band. ±1 day is full credit; ±2 is half.
DAYS_TOLERANCE_FULL = 1
DAYS_TOLERANCE_HALF = 2


# ====================================================================
# Scoring
# ====================================================================

def _score_goal(meta: dict, user_goals: list[str]) -> tuple[int, Optional[str]]:
    """Goal overlap. Awards full WEIGHTS['goal'] for any direct
    overlap; partial credit for tangentially related goals via
    a small synonym map. Returns (points, human-readable reason)."""
    plan_goals = set(meta.get("goals") or [])
    if not plan_goals or not user_goals:
        return (0, None)
    user_set = set(user_goals)
    direct_overlap = plan_goals & user_set
    if direct_overlap:
        first = next(iter(direct_overlap))
        return (
            WEIGHTS["goal"],
            f"Matches your goal: {first.replace('_', ' ')}",
        )
    # Synonym map — tangentially-aligned goals get half credit.
    synonyms = {
        "build_muscle":    {"get_stronger"},
        "get_stronger":    {"build_muscle"},
        "lose_fat":        {"stay_consistent"},
        "stay_consistent": {"lose_fat", "build_muscle"},
        "train_for_sport": {"get_stronger", "stay_consistent"},
    }
    for ug in user_goals:
        related = synonyms.get(ug, set())
        if related & plan_goals:
            return (
                WEIGHTS["goal"] // 2,
                f"Aligned with your goal: {ug.replace('_', ' ')}",
            )
    return (0, None)


def _score_experience(meta: dict, user_exp: str) -> tuple[int, Optional[str]]:
    """Experience match. 'any' matches anything; specific levels
    must match exactly (or differ by one tier). Returns
    (points, reason)."""
    plan_exp = (meta.get("experience") or "").strip().lower()
    user_exp = (user_exp or "").strip().lower()
    if not plan_exp or plan_exp == "any":
        return (
            WEIGHTS["experience"] // 2,
            "Suitable for any experience level",
        )
    if not user_exp:
        return (0, None)
    if plan_exp == user_exp:
        return (
            WEIGHTS["experience"],
            f"Matches your experience: {user_exp.replace('_', ' ')}",
        )
    # One-tier difference gets partial credit. Define an ordering.
    tiers = ["just_starting", "under_one_year", "one_to_three", "three_plus"]
    try:
        gap = abs(tiers.index(plan_exp) - tiers.index(user_exp))
    except ValueError:
        return (0, None)
    if gap == 1:
        return (WEIGHTS["experience"] // 2, None)
    return (0, None)


def _score_equipment(meta: dict, user_eq: str) -> tuple[int, Optional[str]]:
    """Equipment match. 'any' matches anything; specific values
    must match exactly. 'mixed' on either side counts as a soft
    match (half points) since a user with mixed equipment can
    usually adapt."""
    plan_eq = (meta.get("equipment") or "").strip().lower()
    user_eq = (user_eq or "").strip().lower()
    if not plan_eq or plan_eq == "any":
        return (WEIGHTS["equipment"] // 2, "Equipment-flexible")
    if not user_eq:
        return (0, None)
    if plan_eq == user_eq:
        return (
            WEIGHTS["equipment"],
            f"Matches your equipment: {user_eq.replace('_', ' ')}",
        )
    if "mixed" in (plan_eq, user_eq):
        return (WEIGHTS["equipment"] // 2, "Equipment partial match")
    return (0, None)


def _score_days(meta: dict, user_days: int) -> tuple[int, Optional[str]]:
    """Days/week match. Within ±1 day = full; within ±2 = half;
    further = 0."""
    plan_days = int(meta.get("days_per_week") or 0)
    if not plan_days or not user_days:
        return (0, None)
    diff = abs(plan_days - user_days)
    if diff <= DAYS_TOLERANCE_FULL:
        return (
            WEIGHTS["days"],
            f"Matches your days/week: {user_days}",
        )
    if diff <= DAYS_TOLERANCE_HALF:
        return (
            WEIGHTS["days"] // 2,
            f"Close to your days/week: {user_days} vs {plan_days}",
        )
    return (0, None)


def _score_tags(meta: dict, profile_inputs: dict) -> tuple[int, list[str]]:
    """recommended_for / not_recommended_for tag scoring. The tag
    payload on a programme is a list of plain strings curated when
    the catalog was seeded, e.g. ['beginner-friendly',
    'home-gym-friendly', 'minimalist'].

    For now we take a simple approach: if any tag string CONTAINS
    a substring matching the user's goal/experience/equipment in
    a normalised form, count it. This is intentionally loose —
    the curated tags are short and specific, and the recommended
    use is signal not gospel.
    """
    points = 0
    reasons = []

    haystack_terms = []
    for g in (profile_inputs.get("goals") or []):
        haystack_terms.append(g.replace("_", " ").lower())
    if profile_inputs.get("experience"):
        haystack_terms.append(profile_inputs["experience"].replace("_", " ").lower())
    if profile_inputs.get("equipment"):
        haystack_terms.append(profile_inputs["equipment"].replace("_", " ").lower())

    rec_for = [t.lower() for t in (meta.get("recommended_for") or [])]
    not_rec = [t.lower() for t in (meta.get("not_recommended_for") or [])]

    for tag in rec_for:
        if any(term in tag or tag in term for term in haystack_terms if term):
            points += WEIGHTS["recommended_for_bonus"]
            reasons.append(f"Recommended for: {tag}")
            break  # one bonus per programme

    for tag in not_rec:
        if any(term in tag or tag in term for term in haystack_terms if term):
            points += WEIGHTS["not_recommended_for_penalty"]
            reasons.append(f"Not ideal: {tag}")
            break  # one penalty per programme

    return (points, reasons)


def score_programme(meta: dict, profile_inputs: dict) -> tuple[int, list[str]]:
    """Top-level entry point. Returns (total_score, reasons).

    profile_inputs shape:
      {
        "goals":        list[str],
        "experience":   str,
        "equipment":    str,
        "days_per_week": int,
      }
    """
    if not isinstance(meta, dict):
        return (0, [])

    total = 0
    reasons: list[str] = []

    p, r = _score_goal(meta, profile_inputs.get("goals") or [])
    total += p
    if r:
        reasons.append(r)

    p, r = _score_experience(meta, profile_inputs.get("experience") or "")
    total += p
    if r:
        reasons.append(r)

    p, r = _score_equipment(meta, profile_inputs.get("equipment") or "")
    total += p
    if r:
        reasons.append(r)

    p, r = _score_days(meta, int(profile_inputs.get("days_per_week") or 0))
    total += p
    if r:
        reasons.append(r)

    p, rs = _score_tags(meta, profile_inputs)
    total += p
    reasons.extend(rs)

    # Floor at 0; ceil isn't necessary because the only negative
    # contributor is the not_recommended_for penalty.
    total = max(0, total)
    return (total, reasons)


def rank_programmes(
    programmes: list,            # list of (plan, meta) tuples or dicts
    profile_inputs: dict,
    *,
    top_n: int = 3,
) -> tuple[list, list]:
    """Score every programme + return (top_n recommended, the rest
    in original order).

    Each item in `programmes` should expose a `programme_meta`
    attribute (Django model) OR be a dict with a 'programme_meta'
    key. The function is permissive on shape so it can sit between
    the ORM and the iOS layer.

    Returns (recommended, others). Each list item is annotated
    with `_score` and `_reasons` keys when items are dicts, or
    surfaced via the second tuple element when items are objects.
    """
    scored = []
    for p in programmes:
        meta = (
            p.get("programme_meta") if isinstance(p, dict)
            else getattr(p, "programme_meta", {})
        ) or {}
        score, reasons = score_programme(meta, profile_inputs)
        scored.append((score, reasons, p))

    # Stable sort: by score desc, then by original order preserved
    # via enumeration index for ties.
    scored_with_index = list(enumerate(scored))
    scored_with_index.sort(key=lambda t: (-t[1][0], t[0]))

    recommended_with_meta = scored_with_index[:top_n]
    rest_with_meta = scored_with_index[top_n:]

    # Strip back to the items but in the new order, with reasons
    # attached as a second-tuple element so the caller can use them.
    recommended = [(t[1][2], t[1][0], t[1][1]) for t in recommended_with_meta]
    others      = [(t[1][2], t[1][0], t[1][1]) for t in rest_with_meta]

    return (recommended, others)
