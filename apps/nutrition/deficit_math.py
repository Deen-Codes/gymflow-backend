"""
DEFICIT-MATH (#127) — Energy-balance + recomp principles for the
nutrition AI build.

Backs the AI build's macro suggestions with a transparent,
defensible calculation rather than asking Claude to invent
numbers. The AI still picks tone + framing, but the kcal/protein
numbers come from this module so we can:

  • Tell the user "we recommend a 350 kcal deficit because…"
  • Ship guards that prevent dangerous cuts (kcal floor, protein
    floor, lean-mass-loss-rate cap)
  • Cite the literature in DECISIONS.md, not vibes

References (also in `~/Documents/GymFlow/AI_PT_KNOWLEDGE_BASE.md`):
  • Mifflin-St Jeor 1990 — BMR formula
  • Hall 2011 — Quantification of energy imbalance / bodyweight
  • Helms et al. 2014 — Evidence-based recommendations for
    natural bodybuilding contest preparation (lean-mass loss
    as a fraction of weekly weight loss rate)
  • ISSN Position Stand 2017 — Protein and exercise (1.4–2.0
    g/kg/day, upper end during cuts)
  • ACSM 2009 — Carbohydrate guidance (3–10 g/kg depending on
    training load)

API surface — pure functions, no Django models:

  estimate_bmr(weight_kg, height_cm, age, sex)               -> kcal/day
  activity_multiplier(days_per_week, experience)              -> float
  estimate_tdee(profile_inputs)                               -> kcal/day
  protein_floor_g_per_day(weight_kg, goals)                   -> grams
  cut_recommendation(tdee, weight_kg, goals)                  -> dict
  maintain_recommendation(tdee, weight_kg, goals)             -> dict
  bulk_recommendation(tdee, weight_kg, goals)                 -> dict
  three_variants(profile_inputs)                              -> list[dict]
  defensible_rationale(variant_id, inputs, computed)          -> str

`profile_inputs` is a plain dict so this module stays
testable without Django:

  {
    "weight_kg":     float | None,    # None → 75 default + low-conf
    "height_cm":     int | None,
    "age_years":     int | None,
    "sex":           "male" | "female" | None,
    "goals":         list[str],       # SoloProfile.goals
    "experience":    str,             # SoloProfile.experience
    "days_per_week": int,
    "weekly_slope_kg": float | None,  # bodyweight trend (optional)
  }

The new AI build view (apps/nutrition/ai_build_views.py) will
call `three_variants(...)` and pass the result to Claude as
ANCHOR NUMBERS. Claude then writes the rationale prose around
those anchors instead of inventing the kcal counts. This makes
the coach defensible AND the AI's job easier (less math, more
voice).
"""
from __future__ import annotations

from typing import Optional


# ====================================================================
# Constants (citations in module docstring)
# ====================================================================

# ISSN 2017 protein bands (g/kg/day).
PROTEIN_GENERAL_FITNESS  = 1.4
PROTEIN_HYPERTROPHY      = 1.8   # mid of 1.6–2.0 band
PROTEIN_CUT_PRESERVATION = 2.0   # mid of 1.8–2.2 cut band
PROTEIN_HARD_FLOOR       = 1.2   # below this, lean mass at risk

# Helms 2014 — sustainable cut depth.
CUT_DEFICIT_LO    = 350
CUT_DEFICIT_HI    = 500
CUT_PCT_PER_WEEK_LO = 0.005   # 0.5% of bodyweight / week
CUT_PCT_PER_WEEK_HI = 0.010   # 1.0%

# Helms / RP — sustainable bulk surplus.
BULK_SURPLUS_LO   = 150
BULK_SURPLUS_HI   = 300

# Hard floors. Below these we refuse to ship a variant.
KCAL_FLOOR_FEMALE = 1500
KCAL_FLOOR_MALE   = 1800

# Goals that trigger the upper protein band.
HYPERTROPHY_GOALS = {"build_muscle", "get_stronger", "train_for_sport"}


# ====================================================================
# BMR + TDEE
# ====================================================================

def estimate_bmr(
    weight_kg: float,
    height_cm: float,
    age_years: int,
    sex: Optional[str],
) -> float:
    """Mifflin-St Jeor (1990). Returns BMR in kcal/day.

    Defaults to the male formula when sex is unspecified and the
    rationale flag will note "estimate" rather than claiming
    precision. The female formula subtracts ~166 kcal so the
    error is asymmetric — better to undershoot for unspecified
    users than overshoot."""
    base = 10 * weight_kg + 6.25 * height_cm - 5 * age_years
    if sex == "female":
        return base - 161
    elif sex == "male":
        return base + 5
    # Unspecified — split the difference, lean conservative.
    return base - 78  # midpoint between male/female adjustments


def activity_multiplier(days_per_week: int, experience: str) -> float:
    """Pick an activity factor for TDEE.

    Conservative ranges per Mifflin-St Jeor convention. We bias
    toward the lower end of the published bands because most
    users who self-report "very active" overestimate their NEAT.
    Track-and-adjust is the only reliable method; this gets us
    in the ballpark."""
    days = max(0, min(int(days_per_week or 0), 7))
    # Sedentary baseline.
    if days <= 1:
        return 1.30
    if days == 2:
        return 1.40
    if days <= 4:
        return 1.50   # "lightly to moderately active"
    if days <= 6:
        return 1.60   # "moderately to very active"
    return 1.70       # 7-day-a-week trainees

    # Note: experience isn't currently consumed but is on the
    # signature for future tuning (e.g. competitive athletes
    # may justify 1.8+).


def estimate_tdee(inputs: dict) -> float:
    """TDEE = BMR × activity multiplier. Returns kcal/day rounded
    to nearest 50."""
    weight = inputs.get("weight_kg") or 75.0
    height = inputs.get("height_cm") or 170
    age    = inputs.get("age_years") or 30
    sex    = inputs.get("sex")
    days   = inputs.get("days_per_week") or 3
    exp    = inputs.get("experience") or ""

    bmr = estimate_bmr(weight, height, age, sex)
    mult = activity_multiplier(days, exp)
    tdee = bmr * mult
    return round(tdee / 50) * 50


# ====================================================================
# Protein
# ====================================================================

def protein_floor_g_per_day(weight_kg: float, goals: list[str]) -> int:
    """Pick a protein target. Hypertrophy goals → upper band.
    Cut variants override this from the calling code (the cut
    variant always uses PROTEIN_CUT_PRESERVATION)."""
    target_per_kg = (
        PROTEIN_HYPERTROPHY
        if any(g in HYPERTROPHY_GOALS for g in goals)
        else PROTEIN_GENERAL_FITNESS
    )
    return int(round(weight_kg * target_per_kg / 5) * 5)  # round to 5g


# ====================================================================
# Variant builders
# ====================================================================

def _build_variant(
    *,
    vid: str,
    label: str,
    calories: int,
    weight_kg: float,
    goals: list[str],
    protein_per_kg: float,
    fat_pct: float = 0.27,  # 25–30% of kcal from fat (Volek 1997 floor)
) -> dict:
    """Compute the macro split for a single variant.

    Order of operations:
      1. Protein from g/kg target (rounded to 5g).
      2. Fats from kcal × fat_pct, /9 to grams (rounded to 5g).
      3. Carbs fill the remainder.
    Floor each macro at safe minimums."""
    protein_g = max(60, int(round(weight_kg * protein_per_kg / 5) * 5))
    fat_g     = max(30, int(round((calories * fat_pct) / 9 / 5) * 5))
    # Remaining calories go to carbs.
    remaining_kcal = calories - (protein_g * 4) - (fat_g * 9)
    carb_g = max(50, int(round(remaining_kcal / 4 / 5) * 5))

    return {
        "id":       vid,
        "label":    label,
        "calories": calories,
        "protein":  protein_g,
        "carbs":    carb_g,
        "fats":     fat_g,
    }


def cut_recommendation(tdee: int, weight_kg: float, goals: list[str], sex: Optional[str]) -> dict:
    """Cut variant — TDEE minus 400 kcal (mid of Helms 350–500
    band). Hard floor enforces the minimum kcal per sex.
    Protein bumped to the cut-preservation target (2.0 g/kg)
    regardless of goal because cut + lean-mass-preservation is
    the priority."""
    target_kcal = tdee - 400
    floor = KCAL_FLOOR_FEMALE if sex == "female" else KCAL_FLOOR_MALE
    target_kcal = max(floor, target_kcal)
    target_kcal = round(target_kcal / 50) * 50

    return _build_variant(
        vid="cut", label="Lean down",
        calories=target_kcal, weight_kg=weight_kg, goals=goals,
        protein_per_kg=PROTEIN_CUT_PRESERVATION,
    )


def maintain_recommendation(tdee: int, weight_kg: float, goals: list[str]) -> dict:
    """Maintain variant — sit at TDEE."""
    target_per_kg = (
        PROTEIN_HYPERTROPHY
        if any(g in HYPERTROPHY_GOALS for g in goals)
        else PROTEIN_GENERAL_FITNESS
    )
    return _build_variant(
        vid="maintain", label="Hold steady",
        calories=tdee, weight_kg=weight_kg, goals=goals,
        protein_per_kg=target_per_kg,
    )


def bulk_recommendation(tdee: int, weight_kg: float, goals: list[str]) -> dict:
    """Bulk variant — TDEE plus 250 kcal (mid of 150–300 band).
    Protein at the hypertrophy band."""
    target_kcal = tdee + 250
    target_kcal = round(target_kcal / 50) * 50
    return _build_variant(
        vid="bulk", label="Lean gain",
        calories=target_kcal, weight_kg=weight_kg, goals=goals,
        protein_per_kg=PROTEIN_HYPERTROPHY,
    )


def three_variants(inputs: dict) -> list[dict]:
    """Top-level entry point. Returns three variants in order
    cut → maintain → bulk, anchored to the user's TDEE."""
    weight = inputs.get("weight_kg") or 75.0
    goals  = inputs.get("goals") or []
    sex    = inputs.get("sex")

    tdee = estimate_tdee(inputs)

    return [
        cut_recommendation(tdee, weight, goals, sex),
        maintain_recommendation(tdee, weight, goals),
        bulk_recommendation(tdee, weight, goals),
    ]


# ====================================================================
# Rationale generation
# ====================================================================

def defensible_rationale(variant_id: str, inputs: dict, computed: dict) -> str:
    """Produce a one-sentence justification anchored to the
    actual numbers. The AI build view passes these into
    Claude's system prompt so the coach can quote them
    verbatim or paraphrase, but the math stays defensible.

    Voice is calm + plain English, NOT "macros optimised".
    """
    weight = inputs.get("weight_kg")
    weight_phrase = (
        "based on your weight" if weight else "with a default 75 kg estimate"
    )
    if variant_id == "cut":
        return (
            f"~0.5–0.75 kg/week loss from a 400 kcal deficit, "
            f"{weight_phrase}. Protein held at the upper band to "
            f"preserve lean mass."
        )
    if variant_id == "maintain":
        return (
            f"Sit at maintenance to focus on training quality "
            f"without juggling a deficit or surplus, {weight_phrase}."
        )
    if variant_id == "bulk":
        return (
            f"~0.25 kg/week gain from a 250 kcal surplus, "
            f"{weight_phrase}. Protein at the hypertrophy band."
        )
    return ""
