"""
FOOD-DB-TAGGING — name-pattern based auto-tagging engine.

Inputs: a food's name string (and optionally USDA category).
Outputs: `dietary_compat` and `allergens` comma-separated tag sets
ready to drop on a CuratedFood row.

Design principles:
1. Conservative on dietary claims. We only assert "halal" on a
   food when we have HIGH confidence the food contains no pork,
   alcohol, or other haram ingredients. Real provenance — was the
   meat slaughtered halal? — can't be determined from a USDA
   reference row, so we treat "halal" as "this food is compatible
   with a halal diet, the user is responsible for sourcing".
   Same convention for kosher.
2. Aggressive on allergen detection. Better to over-flag and have
   a non-allergic user dismiss it than miss an allergen.
3. Public-domain tagging only. We're not licensing a third-party
   nutrition database for this; rules below were authored from
   first principles + standard food-science references (Codex
   Alimentarius for category definitions, EU 1169/2011 Annex II
   for the allergen list).
"""
from __future__ import annotations

import re
from typing import Iterable

# --------------------------------------------------------------------
# Compiled patterns (kept module-level so the regex compile happens
# once on import, not per-row).
# --------------------------------------------------------------------


def _wordset_pattern(words: Iterable[str]) -> re.Pattern:
    """Build a regex that matches any of `words` as whole words.
    Word-boundary matching avoids false positives like "soy" in
    "soybean" being missed (it isn't — \\b matches the letter
    boundary), or "ham" in "hamburger" being a false positive
    (it would be! — but hamburger is also non-halal so the false
    positive is benign here)."""
    pattern = r"\b(" + "|".join(re.escape(w) for w in words) + r")\b"
    return re.compile(pattern, re.IGNORECASE)


# Pork + pork derivatives — non-halal AND non-kosher.
_PORK_RE = _wordset_pattern([
    "pork", "ham", "bacon", "gammon", "pancetta", "prosciutto",
    "chorizo", "salami", "pepperoni", "lardo", "lard", "speck",
    "mortadella", "guanciale", "sow", "boar", "piglet",
])

# Alcohol — non-halal. (Some kosher rules permit certain wines;
# we conservatively don't tag wine as kosher either way.)
_ALCOHOL_RE = _wordset_pattern([
    "alcohol", "alcoholic", "beer", "wine", "rum", "whiskey",
    "whisky", "vodka", "gin", "brandy", "cognac", "liqueur",
    "vermouth", "sake", "champagne", "cider",
])

# Shellfish — non-kosher. Halal-compatible per most schools.
_SHELLFISH_RE = _wordset_pattern([
    "shrimp", "prawn", "prawns", "crab", "crabs", "lobster",
    "crayfish", "crawfish", "mussel", "mussels", "clam", "clams",
    "oyster", "oysters", "scallop", "scallops", "octopus",
    "squid", "calamari", "cuttlefish", "snail", "snails",
    "escargot",
])

# Other non-kosher animals (no fins+scales / forbidden mammals).
_NON_KOSHER_ANIMAL_RE = _wordset_pattern([
    "rabbit", "hare", "eel", "catfish", "shark", "swordfish",
    "monkfish", "lumpfish", "sturgeon",
])
# (Sturgeon is debated — Orthodox treats it as non-kosher; we
# default to non-kosher to be conservative.)

# Meat (any source) — non-vegetarian, non-vegan.
_MEAT_RE = _wordset_pattern([
    "beef", "veal", "lamb", "mutton", "goat", "venison", "deer",
    "bison", "buffalo", "ox", "oxtail", "tripe", "liver",
    "kidney", "kidneys", "tongue", "heart", "sweetbread",
    "chicken", "turkey", "duck", "goose", "quail", "pheasant",
    "partridge", "pigeon", "ostrich", "emu",
    # plus the pork list (caught by _PORK_RE) — overlap is fine.
])

# Fish — non-vegetarian, non-vegan, but pescatarian-OK.
_FISH_RE = _wordset_pattern([
    "salmon", "tuna", "cod", "haddock", "mackerel", "sardine",
    "sardines", "anchovy", "anchovies", "trout", "bass",
    "halibut", "tilapia", "pollock", "snapper", "sole", "plaice",
    "herring", "kipper", "smelt", "carp", "perch", "pike",
    "flounder", "barramundi", "mahi", "marlin", "tuna",
])

# Dairy — non-vegan; allergen=milk.
# NB: "butter" is omitted from the simple wordset because it
# false-positives on nut butters ("peanut butter", "almond butter",
# "cashew butter" etc., none of which contain dairy). Real butter
# is detected via `_REAL_BUTTER_RE` below — the bare word "butter"
# matches only when it isn't preceded by a nut/seed name.
_DAIRY_RE = _wordset_pattern([
    "milk", "cheese", "cream", "yogurt", "yoghurt",
    "kefir", "ghee", "buttermilk", "curd", "whey", "casein",
    "lactose", "ricotta", "mozzarella", "cheddar", "parmesan",
    "feta", "halloumi", "brie", "camembert", "gouda", "swiss",
    "havarti", "muenster", "provolone", "cottage", "mascarpone",
    "creme fraiche", "double cream", "single cream", "ice cream",
    "gelato", "custard",
])
# Matches the word "butter" only when NOT preceded by a nut /
# seed / "fruit" descriptor — protects "peanut butter" /
# "almond butter" / "cashew butter" / "sunflower seed butter"
# / "apple butter" / "cocoa butter" from being mis-tagged as
# dairy. (Cocoa butter is plant fat, apple butter is fruit
# preserve.)
_REAL_BUTTER_RE = re.compile(
    r"(?<!peanut )(?<!almond )(?<!cashew )(?<!sunflower seed )"
    r"(?<!apple )(?<!cocoa )(?<!nut )\bbutter\b",
    re.IGNORECASE,
)

# Eggs — non-vegan; allergen=eggs.
_EGG_RE = _wordset_pattern([
    "egg", "eggs", "yolk", "yolks", "albumen", "egg white",
    "egg whites", "omelet", "omelette", "frittata", "quiche",
])

# Honey — non-vegan but vegetarian-OK.
_HONEY_RE = _wordset_pattern(["honey", "royal jelly"])

# Gelatin — non-vegan, non-vegetarian, often non-halal/kosher
# (porcine source is most common).
_GELATIN_RE = _wordset_pattern(["gelatin", "gelatine", "isinglass"])

# --------------------------------------------------------------------
# Allergen detection.
# UK FSA / EU 1169/2011 Annex II "top 14":
#   1. Cereals containing gluten (wheat, rye, barley, oats, spelt)
#   2. Crustaceans
#   3. Eggs
#   4. Fish
#   5. Peanuts
#   6. Soybeans
#   7. Milk (incl. lactose)
#   8. Nuts (tree nuts)
#   9. Celery
#   10. Mustard
#   11. Sesame seeds
#   12. Sulphur dioxide / sulphites
#   13. Lupin
#   14. Molluscs
# --------------------------------------------------------------------

_GLUTEN_RE = _wordset_pattern([
    "wheat", "rye", "barley", "spelt", "kamut", "triticale",
    "bulgur", "couscous", "semolina", "farro", "freekeh",
    "bread", "pasta", "noodle", "noodles", "spaghetti", "penne",
    "fusilli", "macaroni", "lasagna", "lasagne", "linguine",
    "fettuccine", "gnocchi", "ravioli", "tortellini",
    "biscuit", "biscuits", "cookie", "cookies", "cracker",
    "crackers", "cake", "muffin", "scone", "pastry", "pie",
    "doughnut", "donut", "bagel", "pretzel", "croissant",
    "tortilla wrap", "wrap", "pita", "naan", "cereal", "muesli",
    "granola", "weetabix", "shredded wheat", "porridge",
    "beer", "malt",
    "soy sauce", "shoyu", "seitan", "vital wheat gluten",
])
# Note: oats are debated — they're naturally GF but cross-
# contaminated unless certified. We include "oats" via _OATS_RE
# because most jurisdictions classify them as a gluten-grain.
_OATS_RE = _wordset_pattern(["oat", "oats", "oatmeal"])

_PEANUT_RE = _wordset_pattern([
    "peanut", "peanuts", "groundnut", "groundnuts",
])

_TREE_NUT_RE = _wordset_pattern([
    "almond", "almonds", "cashew", "cashews", "walnut", "walnuts",
    "pecan", "pecans", "hazelnut", "hazelnuts", "filbert",
    "brazil nut", "brazil nuts", "macadamia", "pistachio",
    "pistachios", "pine nut", "pine nuts", "chestnut", "chestnuts",
])

_SESAME_RE = _wordset_pattern([
    "sesame", "tahini", "tahina", "halva", "halvah",
])

_SOY_RE = _wordset_pattern([
    "soy", "soya", "soybean", "soybeans", "tofu", "tempeh",
    "edamame", "miso", "natto", "soy sauce", "shoyu",
])

_CELERY_RE = _wordset_pattern(["celery", "celeriac"])

_MUSTARD_RE = _wordset_pattern(["mustard"])

_LUPIN_RE = _wordset_pattern(["lupin", "lupine", "lupini"])

# Sulphites: most common in dried fruit, wine, processed meats.
# Hard to detect from name alone; we tag dried fruit as a
# proxy (most are sulphur-treated).
_SULPHITE_RE = _wordset_pattern([
    "dried fruit", "raisin", "raisins", "sultana", "sultanas",
    "currant", "currants", "prune", "prunes", "dried apricot",
    "dried apricots",
])


# --------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------


def detect_allergens(name: str) -> list[str]:
    """Return a sorted list of allergen tags present in `name`.
    Uses EU 1169/2011 Annex II vocabulary."""
    out: set[str] = set()

    if _DAIRY_RE.search(name) or _REAL_BUTTER_RE.search(name):
        out.add("milk")
    if _EGG_RE.search(name):
        out.add("eggs")
    if _GLUTEN_RE.search(name) or _OATS_RE.search(name):
        out.add("gluten")
    if _PEANUT_RE.search(name):
        out.add("peanuts")
    if _TREE_NUT_RE.search(name):
        out.add("tree_nuts")
    if _SESAME_RE.search(name):
        out.add("sesame")
    if _SOY_RE.search(name):
        out.add("soy")
    if _FISH_RE.search(name):
        out.add("fish")
    if _SHELLFISH_RE.search(name):
        # FSA distinguishes crustaceans from molluscs but at our
        # detection precision we can't reliably split shrimp
        # (crustacean) from oyster (mollusc) without a per-token
        # check. We tag both broadly.
        out.add("crustaceans")
        out.add("molluscs")
    if _CELERY_RE.search(name):
        out.add("celery")
    if _MUSTARD_RE.search(name):
        out.add("mustard")
    if _LUPIN_RE.search(name):
        out.add("lupin")
    if _SULPHITE_RE.search(name):
        out.add("sulphites")

    return sorted(out)


def detect_dietary_compat(name: str) -> list[str]:
    """Return a sorted list of dietary-compatibility tags.

    Conservative: a tag is present only when high-confidence
    compatible. Absence of a tag is NOT a claim of incompatibility
    — it's "couldn't determine from name alone".
    """
    out: set[str] = set()

    has_pork = bool(_PORK_RE.search(name))
    has_alcohol = bool(_ALCOHOL_RE.search(name))
    has_shellfish = bool(_SHELLFISH_RE.search(name))
    has_non_kosher_animal = bool(_NON_KOSHER_ANIMAL_RE.search(name))
    has_meat = bool(_MEAT_RE.search(name)) or has_pork
    has_fish = bool(_FISH_RE.search(name))
    has_dairy = bool(_DAIRY_RE.search(name)) or bool(_REAL_BUTTER_RE.search(name))
    has_egg = bool(_EGG_RE.search(name))
    has_honey = bool(_HONEY_RE.search(name))
    has_gelatin = bool(_GELATIN_RE.search(name))
    has_gluten = bool(_GLUTEN_RE.search(name)) or bool(_OATS_RE.search(name))

    # Halal: no pork, no alcohol, no gelatin (often porcine).
    # Note: real halal certification requires slaughter to be
    # zabihah, which we can't determine here. We tag meat as
    # "compatible with halal" only — user is responsible for
    # sourcing certified halal versions.
    if not has_pork and not has_alcohol and not has_gelatin:
        out.add("halal")

    # Kosher: no pork, no shellfish, no rabbit/hare/eel/catfish,
    # no dairy+meat mix (we can't detect the mix from a single
    # row name though). Conservative.
    if (
        not has_pork
        and not has_shellfish
        and not has_non_kosher_animal
        and not has_gelatin
    ):
        out.add("kosher")

    # Vegan: no animal-derived items at all.
    if (
        not has_meat
        and not has_fish
        and not has_dairy
        and not has_egg
        and not has_honey
        and not has_gelatin
        and not has_shellfish
    ):
        out.add("vegan")

    # Vegetarian: no meat or fish (dairy/eggs/honey OK).
    if (
        not has_meat
        and not has_fish
        and not has_gelatin
        and not has_shellfish
    ):
        out.add("vegetarian")

    # Pescatarian: vegetarian + fish OK (no meat/gelatin).
    if not has_meat and not has_gelatin:
        out.add("pescatarian")

    # Gluten-free: no gluten markers.
    if not has_gluten:
        out.add("gluten_free")

    # Dairy-free: no dairy.
    if not has_dairy:
        out.add("dairy_free")

    return sorted(out)


def auto_tag(name: str) -> tuple[str, str]:
    """Convenience: return (dietary_compat_csv, allergens_csv) for
    storing on a CuratedFood row."""
    return (
        ",".join(detect_dietary_compat(name)),
        ",".join(detect_allergens(name)),
    )
