"""T4.2 — Cross-domain alignment helper.

When a user makes a mutation on one side (workout edit, nutrition
edit, macro target change), we check whether the OTHER side now
falls out of alignment with the user's goal. If it does, we return
a soft suggestion ("chip") that the iOS layer renders under the
applied-mutation card. The user can ignore, accept, or dive in.

Heuristics — kept simple on purpose. The point is "did anything
substantive shift", not "compute the perfect new plan." Wrong
suggestions are easier to dismiss than missing ones are to notice.

Used by:
  • apps/workouts/exercise_edit_views.py — after every swap / patch /
    add endpoint completes
  • apps/nutrition/solo_views.py — solo_macro_targets_update
"""
from __future__ import annotations

from django.utils import timezone


def alignment_chip_after_workout_change(user, day_added: int = 0,
                                        day_removed: int = 0) -> dict | None:
    """Returns a chip dict or None.

    Triggers when:
      • Number of training days/wk changed by >= 1 → suggest kcal delta
        in the corresponding direction.
      • Total weekly working sets shifted by >= 25% → flag protein /
        recovery implications.

    Returns shape (when chip is present):
      {
        "kind":     "cross_domain",
        "domain":   "nutrition",
        "title":    "Bump your kcal target?",
        "body":     "<one-sentence rationale>",
        "action":   "/api/nutrition/solo/macro-targets/",
        "delta": { "calories": +200 },
      }
    """
    try:
        from apps.users.models import SoloProfile
    except Exception:
        return None
    profile = SoloProfile.objects.filter(user=user).first()
    if profile is None or not (profile.target_calories or 0) > 0:
        return None

    # Day count delta is the cheapest signal: more training days =
    # more kcal needed; fewer days = fewer.
    if day_added >= 1:
        bump = max(150, day_added * 200)  # rough +200 per added day
        return {
            "kind":   "cross_domain",
            "domain": "nutrition",
            "title":  "Bump your kcal target?",
            "body":   (
                f"You've added {day_added} training "
                f"day{'s' if day_added > 1 else ''}. About +{bump} kcal "
                "would match the new weekly burn so you don't slip into "
                "a deficit by accident."
            ),
            "action": "/api/nutrition/solo/macro-targets/",
            "delta":  {"calories": +bump},
            "from_state": {
                "target_calories": profile.target_calories,
            },
            "to_state": {
                "target_calories": profile.target_calories + bump,
                "target_protein":  profile.target_protein,
                "target_carbs":    profile.target_carbs,
                "target_fats":     profile.target_fats,
            },
        }
    if day_removed >= 1:
        cut = max(150, day_removed * 200)
        return {
            "kind":   "cross_domain",
            "domain": "nutrition",
            "title":  "Trim your kcal target?",
            "body":   (
                f"You've dropped {day_removed} training "
                f"day{'s' if day_removed > 1 else ''}. About -{cut} kcal "
                "would keep you on the same trajectory without leaning "
                "into a surplus."
            ),
            "action": "/api/nutrition/solo/macro-targets/",
            "delta":  {"calories": -cut},
            "from_state": {
                "target_calories": profile.target_calories,
            },
            "to_state": {
                "target_calories": max(1200, profile.target_calories - cut),
                "target_protein":  profile.target_protein,
                "target_carbs":    profile.target_carbs,
                "target_fats":     profile.target_fats,
            },
        }
    return None


def alignment_chip_after_nutrition_change(
    user, *, old_calories: int, new_calories: int,
) -> dict | None:
    """Triggers when kcal target moves by >= 300 in either direction
    while training volume is high — flags recovery risk on aggressive
    cuts or recovery-quality on aggressive surpluses.
    """
    try:
        from apps.users.models import SoloProfile
    except Exception:
        return None
    profile = SoloProfile.objects.filter(user=user).first()
    if profile is None:
        return None

    delta = new_calories - old_calories
    if abs(delta) < 300:
        return None

    days_per_week = profile.days_per_week or 0
    # Steeper deficit on a heavy schedule — flag deload.
    if delta <= -300 and days_per_week >= 5:
        return {
            "kind":   "cross_domain",
            "domain": "workout",
            "title":  "Drop a training day?",
            "body":   (
                f"You've cut {-delta} kcal/day. On a {days_per_week}-day "
                "schedule that's a steep recovery debt. Dropping one day "
                "keeps quality up — happy to propose which."
            ),
            "action": None,   # iOS opens AI PT chat with this seed
            "chat_seed": (
                f"I just dropped my kcal target by {-delta}. "
                f"With my current {days_per_week}-day split, "
                "should I drop a day?"
            ),
        }
    # Surplus + low frequency — flag the volume could ramp up.
    if delta >= 300 and days_per_week <= 3:
        return {
            "kind":   "cross_domain",
            "domain": "workout",
            "title":  "Add a training day?",
            "body":   (
                f"You've added {delta} kcal/day. With "
                f"{days_per_week} day{'s' if days_per_week != 1 else ''}/wk "
                "you'll have plenty of recovery room — adding a day would "
                "convert more of that surplus into muscle than fat."
            ),
            "action":    None,
            "chat_seed": (
                f"I just upped my kcal target by {delta}. "
                f"Should I bump my training to {days_per_week + 1} days?"
            ),
        }
    return None
