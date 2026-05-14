"""LIQUID-PORTION-UNITS (May 2026, Deen QC #M14).

Background — `CuratedFood.portion_unit` was added in 0011 with a
default of "grams" because most foods are weighed. That default
silently propagated to every existing row including the milks,
juices, coffees, and other drinks in the GymFlow-curated seed.
Result in the picker: "Whole milk" offered 50g/100g/150g/200g/250g
chips with a "g" suffix, when nobody on Earth measures milk in
grams. The macro math still worked (kcal/100g × grams) but the UX
was wrong.

Fix — flip portion_unit to "ml" and set unit_grams=1.0 for rows
that are unambiguously liquid (water-density beverages where 1g ≈
1ml). The macro math is unchanged: at unit_grams=1.0, "250 ml"
multiplied through the same kcal/100g constant yields the same
calories that "250 g" would have. iOS just renders ml chips.

Detection — pattern-allowlist + pattern-blocklist. The allowlist
catches the ~20 real drinks in the 200-row seed; the blocklist
excludes the well-known "looks-liquid but is solid" cases
(Cadbury Dairy Milk = chocolate bar, Milky Way = candy bar, tea
cake = a pastry, beer batter = a coating, etc.). We don't touch
rows that aren't currently portion_unit="grams" so manual edits
or future overrides are preserved.

Scope — runs against the GymFlow-curated seed. We're building our
own catalog from scratch (no USDA/FSA/AUSNUT/CIQUAL ingest), so
every future row will be added with portion_unit set correctly
at write time. This migration is a one-time fix-up for the existing
rows seeded before portion_unit had liquid awareness.
"""
from __future__ import annotations

import re

from django.db import migrations


# --------------------------------------------------------------------
# Liquid detection
# --------------------------------------------------------------------
#
# Each allowlist entry is a regex matched against the lowercased
# name. Word-boundary anchors keep "milky way" out of the "milk"
# pattern and stop "coffee bean" from matching the "coffee" drink
# pattern.

_LIQUID_PATTERNS = [
    # Plain milks — full-fat through skim, dairy and plant.
    r"\bmilk\b",                          # generic "milk" / "X milk" / "milk, X"

    # Juices.
    r"\bjuice\b",

    # Water (drinking water — not "tuna in water").
    r"^\s*water\b",
    r"\b(sparkling|still|tap|mineral|coconut) water\b",

    # Hot drinks.
    r"\b(coffee|tea|latte|cappuccino|americano|mocha|macchiato|espresso|flat\s+white|cortado|frappuccino|chai)\b",
    r"\bhot chocolate\b",

    # Soft drinks / sodas.
    r"\b(coca[- ]?cola|pepsi|sprite|fanta|7[- ]?up|dr\s*pepper|root beer|ginger ale|tonic water|club soda|cream soda)\b",
    r"\b(soda|cola|lemonade|cordial|squash)\b",

    # Sports + energy drinks.
    r"\b(lucozade|gatorade|powerade|red bull|monster|rockstar|relentless|prime hydration|prime energy)\b",

    # Alcohol.
    r"\b(beer|lager|ale|stout|ipa|cider|wine|champagne|prosecco|whiskey|whisky|vodka|gin|rum|tequila|brandy|cognac|liqueur|cocktail|martini|mojito|negroni|margarita)\b",

    # Other drinks.
    r"\b(smoothie|milkshake|protein shake|meal shake)\b",
    r"\b(oat|soya?|almond|rice|coconut|cashew|hemp) drink\b",

    # Cooking liquids — broths/stocks behave like drinks for portioning.
    r"\b(broth|stock|consomm[ée]|bouillon)\b",
]

# Items that pattern-match as liquid but are actually solid. The
# allowlist catches them via a substring like "milk" or "tea" or
# "beer" but the food itself is something you bite, not drink.
_SOLID_OVERRIDES = [
    # Confectionery using "milk" in the name.
    r"\bdairy milk\b",                    # Cadbury Dairy Milk (chocolate bar)
    r"\bmilk chocolate\b",                # Hershey's etc.
    r"\bmilky way\b",
    r"\bmilk bar\b",                      # any "milk bar" branded confection
    r"\b(milk|tea|coffee|beer|wine)\s+(bar|biscuit|cookie|wafer|chocolate|cake|loaf|roll|bread|bun|pastry|pudding|gum|gummy|gummies|sweet|sweets|powder|crisps?|chips?|brownie)\b",

    # Things made WITH a liquid but are not themselves a drink.
    r"\bporridge\b|\boatmeal\b",
    r"\bcheese\b|\bbutter\b|\byogurt\b|\byoghurt\b|\bcustard\b|\bcream(?! soda)\b",
    r"\bmilk powder\b|\bcoffee bean\b|\btea bag\b|\btea leaf\b|\btea leaves\b",
    r"\bbeer batter\b|\bbeer can chicken\b|\bwine sauce\b|\bcoffee bean\b",
    r"\bjuice (concentrate|powder)\b",    # concentrates are typically logged as grams
]


def _is_liquid(name: str) -> bool:
    """Return True when `name` looks like a drink. Pattern-allowlisted
    AND not solid-overridden."""
    n = (name or "").lower().strip()
    if not n:
        return False
    matched = any(re.search(pat, n) for pat in _LIQUID_PATTERNS)
    if not matched:
        return False
    if any(re.search(pat, n) for pat in _SOLID_OVERRIDES):
        return False
    return True


# --------------------------------------------------------------------
# Forward + reverse operations
# --------------------------------------------------------------------

def _flip_liquids_to_ml(apps, schema_editor):
    CuratedFood = apps.get_model("nutrition", "CuratedFood")

    # Only touch rows that are still on the default. If a row was
    # already manually set (to e.g. "bottle" or "can"), leave it.
    qs = CuratedFood.objects.filter(portion_unit="grams")
    flipped = []
    for f in qs:
        if not _is_liquid(f.name):
            continue
        f.portion_unit = "ml"
        # Water-density liquids: 1g ≈ 1ml. The macro math at log
        # time multiplies (kcal_per_100g × portion / 100) — switching
        # the portion unit's interpretation doesn't change kcal
        # because we keep the numeric value 1:1 with grams.
        f.unit_grams = 1.0
        f.save(update_fields=["portion_unit", "unit_grams"])
        flipped.append(f.id)

    # Audit log so the user can spot-check after migrate. Visible in
    # `manage.py migrate` output on both dev and Render.
    if flipped:
        print(
            f"  [0017_liquid_portion_units] flipped {len(flipped)} rows "
            f"to portion_unit='ml': ids {flipped}"
        )
    else:
        print("  [0017_liquid_portion_units] no rows matched (clean run).")


def _revert_liquids_to_grams(apps, schema_editor):
    """Reverse — flip ml→grams for rows we'd flip forward. Idempotent
    relative to the same data; doesn't touch rows the forward pass
    wouldn't have touched."""
    CuratedFood = apps.get_model("nutrition", "CuratedFood")
    for f in CuratedFood.objects.filter(portion_unit="ml", unit_grams=1.0):
        if _is_liquid(f.name):
            f.portion_unit = "grams"
            f.unit_grams = None
            f.save(update_fields=["portion_unit", "unit_grams"])


class Migration(migrations.Migration):

    dependencies = [
        ("nutrition", "0016_solofoodlogentry_meal_template_item"),
    ]

    operations = [
        migrations.RunPython(_flip_liquids_to_ml, _revert_liquids_to_grams),
    ]
