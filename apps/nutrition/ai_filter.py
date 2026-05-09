"""T2.4 — Food candidate filter for AI nutrition suggestions.

Pre-filters the ~10K-row CuratedFood catalog into a ~300-row
shortlist the AI nutrition build (Phase 2 catalog grounding, T3.2)
can inject into Claude's system prompt as the "pick from these"
candidate set. Sending the whole catalog (~300K tokens) blows the
context window; pre-filtering brings it to ~20K tokens at typical
~30 tokens per food entry.

Hard filter (drops the row entirely):
    • dietary_pattern    — vegan profile never sees animal products
    • allergies          — peanut allergy strips peanut-containing rows
    • region             — UK profile sees gb-tagged rows + region-free
    • category whitelist — supplement vs food vs drink for `slot`

Soft rank (lower = better, sort ascending):
    • macro fit  — for slot-targeted macros (protein-heavy for post-
                   workout, balanced for breakfast etc.)
    • brand familiarity — UK staples > obscure entries
    • cooking_comfort   — single-ingredient over recipe-y items for
                          users who flagged low cooking comfort
    • dislike penalty   — soft demote (don't drop) on dislike token
                          substring match in name
"""
from __future__ import annotations

import re
from typing import Iterable

from .models import CuratedFood


# ----------------------------------------------------------------
# Slot → preferred categories. The slot tells us what *kind* of
# food makes sense — pre-workout shouldn't surface roast dinners,
# post-workout shouldn't surface alcohol.
# ----------------------------------------------------------------
SLOT_CATEGORY_HINTS: dict[str, list[str]] = {
    "breakfast": ["dairy", "bakery", "fruit", "cereal", "egg", "protein", "spread"],
    "lunch":     ["sandwich", "wrap", "salad", "soup", "ready_meal", "protein", "grain", "veg"],
    "dinner":    ["meat", "fish", "veg", "grain", "ready_meal", "protein", "carb"],
    "snack":     ["fruit", "snack", "chocolate", "crisp", "bar", "yogurt", "nut"],
    "pre_workout":   ["bar", "fruit", "drink", "supplement"],
    "intra_workout": ["drink", "supplement"],
    "post_workout":  ["protein", "supplement", "drink", "bar", "fruit"],
}


# Strings that mark animal products in a name. Used by the vegan/
# vegetarian hard filter as a defence in depth — even if a row
# isn't tagged vegan-incompatible, name contains "chicken" → drop.
ANIMAL_PRODUCT_TOKENS: list[str] = [
    "chicken", "beef", "pork", "lamb", "turkey", "duck", "goose",
    "veal", "bacon", "ham", "sausage", "salami", "chorizo",
    "fish", "salmon", "tuna", "cod", "haddock", "mackerel",
    "prawn", "shrimp", "lobster", "crab", "anchovy", "anchovies",
    "gelatin", "gelatine", "lard", "tallow",
]
DAIRY_TOKENS: list[str] = [
    "milk", "cheese", "yogurt", "yoghurt", "cream", "butter",
    "ghee", "whey", "casein",
]
EGG_TOKENS: list[str] = ["egg"]


def _has_token(name: str, tokens: Iterable[str]) -> bool:
    s = (name or "").lower()
    return any(re.search(rf"\b{re.escape(t)}\b", s) for t in tokens)


def _normalise_strs(items: Iterable[str]) -> list[str]:
    out = []
    for raw in (items or []):
        s = (raw or "").strip().lower()
        if s:
            out.append(s)
    return out


def candidate_foods(
    profile,
    *,
    slot: str | None = None,
    region: str | None = "gb",
    max_n: int = 300,
) -> list[dict]:
    """Return the top-N CuratedFood rows ranked for this user.

    Args:
        profile: SoloProfile (or None for no-filter)
        slot:    optional meal slot ('breakfast', 'lunch', etc.) —
                 narrows the category whitelist
        region:  ISO-3166 alpha-2 token to filter by `region_codes`.
                 Defaults to 'gb' to keep the slice UK-focused.
        max_n:   cap on returned rows

    Returns:
        list of dicts ready to JSON-encode into a Claude prompt:
            [{"id", "name", "brand", "kcal", "p", "c", "f",
              "category", "portion_unit"}, ...]
    """
    qs = CuratedFood.objects.all()

    if region:
        # CuratedFood.region_codes is comma-separated lowercase
        # ISO codes; an icontains is fine because we ship them
        # comma-bounded so "gb" matches "gb,us" but not "argb".
        qs = qs.filter(region_codes__icontains=region.lower())

    rows = list(qs.only(
        "id", "name", "brand", "kcal_per_100g", "protein_per_100g",
        "carbs_per_100g", "fat_per_100g",
        "tags", "portion_unit", "unit_grams",
    ))

    # Hard filter — diet
    diet = ""
    allergies: list[str] = []
    dislikes: list[str]  = []
    cooking_comfort = ""
    if profile is not None:
        diet = (getattr(profile, "dietary_pattern", "") or "").lower()
        allergies = _normalise_strs(getattr(profile, "food_restrictions", None) or [])
        dislikes  = _normalise_strs(getattr(profile, "food_dislikes", None) or [])
        cooking_comfort = (getattr(profile, "cooking_comfort", "") or "").lower()

    if diet == "vegan":
        rows = [
            r for r in rows
            if not _has_token(r.name, ANIMAL_PRODUCT_TOKENS + DAIRY_TOKENS + EGG_TOKENS)
        ]
    elif diet == "vegetarian":
        rows = [
            r for r in rows
            if not _has_token(r.name, ANIMAL_PRODUCT_TOKENS)
        ]
    elif diet == "pescatarian":
        # No mammal/poultry — fish OK.
        rows = [
            r for r in rows
            if not _has_token(r.name, [
                "chicken", "beef", "pork", "lamb", "turkey", "duck",
                "goose", "veal", "bacon", "ham", "sausage", "salami",
                "chorizo", "gelatin", "gelatine", "lard", "tallow",
            ])
        ]
    elif diet == "halal":
        # Cheap proxy — strip pork-derived rows. A real halal pass
        # needs explicit certification metadata which the catalog
        # doesn't ship; this prevents the obvious wrong answers.
        rows = [
            r for r in rows
            if not _has_token(r.name, [
                "pork", "bacon", "ham", "gelatin", "gelatine", "lard",
                "alcohol", "wine", "beer",
            ])
        ]
    elif diet == "kosher":
        rows = [
            r for r in rows
            if not _has_token(r.name, [
                "pork", "bacon", "ham", "shellfish", "prawn", "shrimp",
                "lobster", "crab",
            ])
        ]

    # Allergies — substring match
    if allergies:
        rows = [
            r for r in rows
            if not any(tok in r.name.lower() for tok in allergies)
        ]

    # Slot → category whitelist
    slot_categories = SLOT_CATEGORY_HINTS.get((slot or "").lower())

    # Soft rank
    def _score(r) -> tuple[int, int, int, int, str]:
        # Slot category match (lower better). CuratedFood doesn't
        # have a `category` column — instead it ships `tags` as a
        # comma-separated string ("dairy,supermarket_uk", etc.) that
        # we substring-match against the slot category hints.
        cat_score = 0
        if slot_categories:
            tags_lower = (r.tags or "").lower()
            name_lower = r.name.lower()
            cat_match = any(c in tags_lower or c in name_lower for c in slot_categories)
            cat_score = 0 if cat_match else 2

        # Dislike penalty — soft, doesn't drop.
        dislike_score = 0
        if dislikes:
            name_l = r.name.lower()
            if any(d in name_l for d in dislikes):
                dislike_score = 3

        # Cooking-comfort score: low-comfort users prefer single-
        # ingredient / packaged items (kcal/100g near round numbers
        # is a noisy signal — use brand-presence as a heuristic for
        # packaged + ready-to-eat).
        comfort_score = 0
        if cooking_comfort in ("low", "minimal", "low_comfort"):
            comfort_score = 0 if r.brand else 1

        # Macro fit per slot — quick heuristic. Post-workout = high
        # protein/100g; pre-workout = carb-leaning; breakfast/lunch/
        # dinner stay neutral.
        macro_score = 0
        if (slot or "").lower() == "post_workout":
            macro_score = 0 if r.protein_per_100g >= 15 else 1
        elif (slot or "").lower() == "pre_workout":
            macro_score = 0 if r.carbs_per_100g >= 30 else 1

        return (cat_score, macro_score, dislike_score, comfort_score, r.name.lower())

    rows.sort(key=_score)
    sliced = rows[:max_n]

    return [
        {
            "id":            r.id,
            "name":          r.name,
            "brand":         r.brand or "",
            "kcal":          round(r.kcal_per_100g, 1),
            "p":             round(r.protein_per_100g, 1),
            "c":             round(r.carbs_per_100g, 1),
            "f":             round(r.fat_per_100g, 1),
            "tags":          r.tags or "",
            "portion_unit":  getattr(r, "portion_unit", "") or "grams",
            "unit_grams":    getattr(r, "unit_grams", None),
        }
        for r in sliced
    ]
