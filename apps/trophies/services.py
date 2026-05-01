"""Award engine + endpoint helpers.

Two responsibilities:

1. `evaluate_and_award(user)` — runs every evaluator, creates
   ClientTrophyAward rows for any trophy the user has just earned,
   returns the list of newly-earned ones (for the "trophy unlocked"
   reveal on the workout-complete screen).

2. `list_trophies_for(user)` — returns the full catalogue with each
   trophy's earned/locked state and progress, ready for serialisation
   to the iOS Trophies tab.

Both wrap the EVALUATORS dict so the rest of the codebase doesn't
need to know about evaluator internals.
"""
import logging

from .models import ClientTrophyAward, Trophy
from .evaluators import EVALUATORS

log = logging.getLogger(__name__)


def evaluate_and_award(user):
    """Run all evaluators, create awards for newly-earned trophies,
    and return the list of newly-earned `Trophy` instances.

    Idempotent — running this twice in a row produces the same
    awards, no duplicates (uniqueness constraint).
    """
    already_earned_ids = set(
        ClientTrophyAward.objects
        .filter(user=user)
        .values_list("trophy_id", flat=True)
    )

    # Map code → Trophy row so we can resolve evaluators to FK targets
    # in a single query rather than per-trophy lookups.
    catalogue_by_code = {t.code: t for t in Trophy.objects.all()}

    newly_earned = []
    for code, evaluator in EVALUATORS.items():
        trophy = catalogue_by_code.get(code)
        if trophy is None:
            # Catalogue row missing — happens if the seed migration
            # hasn't run yet on this DB. Skip silently rather than
            # crash the workout-save path.
            continue
        if trophy.id in already_earned_ids:
            continue
        try:
            current, target = evaluator(user)
        except Exception as exc:
            # Evaluators should never crash the request that triggered
            # them (e.g. a workout save). Log and move on. Using
            # log.exception captures the full traceback to Render's
            # log stream — the previous print() was invisible there.
            log.exception("trophies evaluator %r failed", code)
            continue
        if current >= target:
            ClientTrophyAward.objects.create(user=user, trophy=trophy)
            newly_earned.append(trophy)

    return newly_earned


def list_trophies_for(user):
    """Return the full catalogue with per-trophy progress + earned-at,
    grouped-friendly for the iOS Trophies tab.

    Crucially, this runs `evaluate_and_award` first so users who
    completed trophies BEFORE the system was deployed (or before a
    new evaluator was wired) get retroactively caught up the moment
    they open their Trophies tab. Without this, the back-catalogue
    sits permanently locked because the workout/check-in event hooks
    only fire on new submissions going forward.

    Output shape (one entry per trophy in the catalogue):
        {
            "code":        "first_workout",
            "name":        "First Workout",
            "description": "...",
            "category":    "workout_volume",
            "rarity":      "common",
            "icon":        "figure.run",
            "earned":      True,
            "earned_at":   "2026-04-27T12:00:00Z" or None,
            "progress":    {"current": 1, "target": 1},
        }
    """
    # Retroactive sweep — idempotent, fast (~100 evaluators), runs
    # quietly. Awards are uniqueness-constrained so a re-run produces
    # no duplicates.
    try:
        evaluate_and_award(user)
    except Exception:
        log.exception("trophies retroactive sweep failed")

    awards_by_trophy_id = {
        a.trophy_id: a for a in
        ClientTrophyAward.objects.filter(user=user).select_related("trophy")
    }

    out = []
    for trophy in Trophy.objects.all():
        evaluator = EVALUATORS.get(trophy.code)
        if evaluator is None:
            current, target = (0, 1)
        else:
            try:
                current, target = evaluator(user)
            except Exception:
                log.exception("trophies list evaluator %r failed", trophy.code)
                current, target = (0, 1)

        award = awards_by_trophy_id.get(trophy.id)
        out.append({
            "code":        trophy.code,
            "name":        trophy.name,
            "description": trophy.description,
            "category":    trophy.category,
            "rarity":      trophy.rarity,
            "icon":        trophy.icon,
            "earned":      award is not None,
            "earned_at":   award.earned_at.isoformat() if award else None,
            "progress": {
                "current": int(min(current, target)),
                "target":  int(target),
            },
        })
    return out
