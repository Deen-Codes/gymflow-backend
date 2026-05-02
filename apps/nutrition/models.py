from django.conf import settings
from django.db import models
from django.utils import timezone


class NutritionPlan(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)

    calories_target = models.IntegerField(default=0)
    protein_target = models.IntegerField(default=0)
    carbs_target = models.IntegerField(default=0)
    fats_target = models.IntegerField(default=0)

    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    # Template vs client-specific versioning
    is_template = models.BooleanField(default=True)
    source_template = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="client_versions",
    )
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="client_specific_nutrition_plans",
    )

    # Phase 5: timestamp for the Activity feed.
    created_at = models.DateTimeField(auto_now_add=True, null=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


# --------------------------------------------------------------------
# NUTRITION-DB (#105) — owned, multi-region food catalog.
#
# Strategy (per repo notes + the ingest plan):
#   • USDA FoodData Central        — US, public domain, ~400k items
#   • UK FSA McCance & Widdowson's — UK, CC BY 4.0, ~3k staples
#   • AUSNUT 2011-13               — AU/NZ, public, ~5.7k items
#   • CIQUAL                       — EU/FR, open license, ~3.2k items
#   • Marrow-curated               — our own additions (branded items
#                                    we add manually; pro-tier fields)
#
# Open Food Facts is intentionally NOT ingested (CC BY-SA copyleft
# blocks commercial bake-in). OFF stays as a runtime barcode lookup
# in iOS — `FoodLookupService` already handles that path. Branded
# items the user finds via barcode and re-uses become entries in the
# user's per-account food log; popular ones can be promoted into the
# `marrow` source over time with our own re-derivation.
#
# Multi-region resolution: when the iOS client searches, the backend
# filters CuratedFood by `region_codes` overlap with the user's locale
# (`Locale.current.region`). Items tagged with multiple regions
# ("chicken breast" — US/UK/AU/EU) appear in every market. Items
# specific to one region ("Tesco Greek yogurt") only appear there.
#
# Ingest pipeline lives in `management/commands/import_curated_foods.py`
# (scaffolded; data loaders to be added per source).
# --------------------------------------------------------------------


class CuratedFood(models.Model):
    """Owned food catalog row.

    Read-only at runtime — iOS/web search this table; updates happen
    via the management command. The `source` field is the provenance
    so future re-ingests can `update_or_create` against `(source,
    source_id)` without duplicating rows.
    """

    SOURCE_USDA   = "usda"
    SOURCE_FSA_UK = "fsa_uk"
    SOURCE_AUSNUT = "ausnut"
    SOURCE_CIQUAL = "ciqual"
    SOURCE_MARROW = "marrow"
    SOURCE_CHOICES = [
        (SOURCE_USDA,   "USDA FoodData Central"),
        (SOURCE_FSA_UK, "UK FSA McCance & Widdowson's"),
        (SOURCE_AUSNUT, "AUSNUT 2011-13"),
        (SOURCE_CIQUAL, "CIQUAL"),
        (SOURCE_MARROW, "Marrow curated"),
    ]

    source     = models.CharField(max_length=12, choices=SOURCE_CHOICES, db_index=True)
    source_id  = models.CharField(max_length=64, db_index=True)
    name       = models.CharField(max_length=200, db_index=True)
    brand      = models.CharField(max_length=120, blank=True, default="")
    barcode    = models.CharField(max_length=32, blank=True, default="", db_index=True)

    # Region distribution. Stored as a comma-separated lowercase ISO
    # 3166-1 alpha-2 list (`"us,gb,au"`). Comma-separated rather than
    # ArrayField so we don't pin the catalog to Postgres-only — SQLite
    # works for local dev. iOS sends its locale, backend does a
    # `__contains` filter on the comma-bounded list.
    region_codes = models.CharField(max_length=64, blank=True, default="")

    # Macros per 100g (cooked / as-eaten where relevant). Each source
    # publishes its own as-prepared semantics; ingest normalises to
    # 100g of the form a user typically eats (cooked rice, not raw).
    kcal_per_100g    = models.FloatField()
    protein_per_100g = models.FloatField()
    carbs_per_100g   = models.FloatField()
    fat_per_100g     = models.FloatField()

    # Default serving info — what the user is likely to log. Falls
    # back to 100g if a source doesn't specify.
    serving_grams = models.FloatField(null=True, blank=True)
    serving_label = models.CharField(max_length=40, blank=True, default="")

    # Free-form tags for filtering UI ("staple", "branded", "fast_food",
    # "high_protein", etc.). Comma-separated lowercase.
    tags = models.CharField(max_length=200, blank=True, default="")

    # FOOD-DB-TAGGING — structured tags so the AI builder, swap
    # mutations, and dietary filters don't have to substring-match
    # the freeform `tags` column.
    #
    # `dietary_compat` — comma-separated lowercase set drawn from:
    #   halal, kosher, vegan, vegetarian, pescatarian,
    #   gluten_free, dairy_free
    # Conservative semantics: a tag is ONLY present when the food
    # is provably compatible. "halal" on chicken means "compatible
    # with halal diet (no pork, no alcohol)" — NOT "this specific
    # chicken was slaughtered halal". Real provenance is the user's
    # responsibility on branded items; for our purposes the absence
    # of pork/alcohol is the surfaceable signal.
    dietary_compat = models.CharField(max_length=128, blank=True, default="", db_index=True)

    # `allergens` — UK FSA "top 14" (the EU allergen set):
    #   milk, eggs, gluten, peanuts, tree_nuts, sesame, soy,
    #   fish, crustaceans, molluscs, celery, mustard, lupin,
    #   sulphites
    # Comma-separated, lowercase. Empty string means "no known
    # major allergens detected" — NOT "verified allergen-free".
    # Branded items always need real label data; this column is
    # the safety floor for AI-generated meal plans.
    allergens = models.CharField(max_length=128, blank=True, default="", db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("source", "source_id")]
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["barcode"]),
        ]

    def __str__(self) -> str:
        return f"[{self.source}] {self.name}"


class FoodLibraryItem(models.Model):
    """Per-trainer food preset.

    `reference_grams` + macros define the per-portion nutritional values
    (e.g. "100g of brown rice = 360 kcal / 7p / 75c / 3f"). Phase 3 adds
    optional `source`, `external_id`, `brand` so the same row can either
    be a custom item the trainer typed in OR a snapshot of an Open Food
    Facts product they pulled from search.
    """

    SOURCE_CUSTOM = "custom"
    SOURCE_OFF = "off"
    SOURCE_CHOICES = [
        (SOURCE_CUSTOM, "Custom"),
        (SOURCE_OFF, "Open Food Facts"),
    ]

    # Portion mode. For weighed foods (rice, chicken) `reference_grams`
    # carries the actual weight reference. For named non-gram portions
    # (tbsp, cup, ml, oz) `reference_grams` is repurposed to mean
    # "reference amount" — e.g. 1 tbsp, 1 cup — and macros are stored
    # per that unit. The freeform PORTION_UNIT slot is for everything
    # else (eggs, wraps, scoops, slices) where `unit_label` carries
    # the noun.
    #
    # The column is named `reference_grams` for legacy reasons; we
    # don't rename it because doing so would cascade across every
    # serializer / view / migration. The semantic is now "reference
    # amount in the chosen portion units."
    PORTION_GRAMS = "grams"
    PORTION_ML    = "ml"
    PORTION_OUNCE = "oz"
    PORTION_TBSP  = "tbsp"
    PORTION_TSP   = "tsp"
    PORTION_CUP   = "cup"
    PORTION_UNIT  = "unit"
    PORTION_CHOICES = [
        (PORTION_GRAMS, "Grams"),
        (PORTION_ML,    "Millilitres"),
        (PORTION_OUNCE, "Ounces"),
        (PORTION_TBSP,  "Tablespoon"),
        (PORTION_TSP,   "Teaspoon"),
        (PORTION_CUP,   "Cup"),
        (PORTION_UNIT,  "Unit (egg, scoop, slice…)"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="food_library_items",
    )
    name = models.CharField(max_length=255)
    reference_grams = models.FloatField(default=100)
    calories = models.FloatField(default=0)
    protein = models.FloatField(default=0)
    carbs = models.FloatField(default=0)
    fats = models.FloatField(default=0)

    portion_type = models.CharField(
        max_length=10,
        choices=PORTION_CHOICES,
        default=PORTION_GRAMS,
    )
    unit_label = models.CharField(max_length=40, blank=True, default="")

    # Phase 3 metadata
    source = models.CharField(
        max_length=20, choices=SOURCE_CHOICES, default=SOURCE_CUSTOM
    )
    external_id = models.CharField(max_length=64, blank=True, default="")
    brand = models.CharField(max_length=255, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "source", "external_id"],
                condition=~models.Q(external_id=""),
                name="unique_food_library_external_per_trainer",
            ),
        ]

    def __str__(self):
        return self.name


# ----------------------------------------------------------------------
# SOLO-only nutrition tracking (N.1.1)
#
# Solo users don't have a trainer-built meal plan. They log foods
# ad-hoc against macro targets (the MyFitnessPal pattern). This model
# is intentionally separate from NutritionMeal/NutritionMealItem
# (which are plan-template rows) so the trainer-coded surface stays
# untouched.
#
# A row = "I ate X grams of food Y on date Z."
# ----------------------------------------------------------------------


class SoloFoodLogEntry(models.Model):
    """One logged food a Solo user ate on one day."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="solo_food_log",
    )

    # Either a FoodLibraryItem reference (typed once, reused) OR an
    # ad-hoc snapshot (the user typed in a one-off food). When `food`
    # is set, name + macros are derived from it; when null, the
    # snapshot fields below carry the data.
    food = models.ForeignKey(
        "FoodLibraryItem",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="solo_log_entries",
    )

    # Snapshot fields — populated whenever a row is created so the
    # log stays valid even if the FoodLibraryItem is later deleted.
    # If `food` is null, these are the only source of truth.
    name        = models.CharField(max_length=255)
    portion     = models.FloatField(default=100)   # in the food's portion units
    calories    = models.FloatField(default=0)
    protein     = models.FloatField(default=0)
    carbs       = models.FloatField(default=0)
    fats        = models.FloatField(default=0)

    consumed_on = models.DateField(db_index=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-consumed_on", "-created_at"]
        indexes = [
            models.Index(fields=["user", "consumed_on"]),
        ]

    def __str__(self):
        return f"{self.user_id} — {self.name} ({self.consumed_on})"


class NutritionMeal(models.Model):
    nutrition_plan = models.ForeignKey(
        NutritionPlan,
        on_delete=models.CASCADE,
        related_name="meals",
    )
    title = models.CharField(max_length=100)
    order = models.IntegerField()

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.nutrition_plan.name} - {self.title}"


class NutritionMealItem(models.Model):
    meal = models.ForeignKey(
        NutritionMeal,
        on_delete=models.CASCADE,
        related_name="items",
    )
    food_library_item = models.ForeignKey(
        FoodLibraryItem,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="meal_items",
    )

    # Snapshot fields so the meal stays stable even if the food preset changes later
    food_name = models.CharField(max_length=255)
    reference_grams = models.FloatField(default=100)
    grams = models.FloatField(default=0)

    # Portion mode snapshot — copied from the FoodLibraryItem at add time
    # so changing the preset later doesn't retroactively rewrite this
    # client's planned meals.
    portion_type = models.CharField(
        max_length=10,
        choices=FoodLibraryItem.PORTION_CHOICES,
        default=FoodLibraryItem.PORTION_GRAMS,
    )
    unit_label = models.CharField(max_length=40, blank=True, default="")
    # Quantity in units (eggs, wraps...). Only meaningful when
    # portion_type == "unit"; otherwise leave at 0 and use `grams`.
    units = models.FloatField(default=0)

    calories = models.FloatField(default=0)
    protein = models.FloatField(default=0)
    carbs = models.FloatField(default=0)
    fats = models.FloatField(default=0)

    order = models.IntegerField(default=1)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.meal.title} - {self.food_name}"


# ============================================================
# Phase C.2 — Server-side meal consumption tracking
#
# Replaces the iOS-local UserDefaults `MealConsumptionStore`.
# Two granularities supported by a single table:
#
#   • Item-level tick:  meal_item points at a specific
#     NutritionMealItem. Used when the client checks individual
#     foods inside a meal ("ate the chicken but not the rice").
#
#   • Meal-level tick:  meal_item is null. Used when the client
#     marks the whole meal as eaten via the meal-card tick.
#
# Why one table and not two:
#   - Same query shape ("what did this client tick today?")
#   - Trainer dashboard renders a unified feed; storing both as
#     rows in one table makes the SQL trivial.
#   - Conditional UniqueConstraint enforces "no double-tick of the
#     same item-on-day" without forbidding the meal-level row from
#     coexisting with item-level rows of the same meal (which is
#     a legit "I ticked individual items, then also said 'meal done'"
#     state — though we'd typically dedupe in the iOS layer).
# ============================================================
class NutritionMealConsumption(models.Model):
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="meal_consumptions",
    )
    meal = models.ForeignKey(
        NutritionMeal,
        on_delete=models.CASCADE,
        related_name="consumptions",
    )
    # null  → meal-level tick (whole meal marked as eaten)
    # bound → item-level tick (specific food inside the meal)
    meal_item = models.ForeignKey(
        NutritionMealItem,
        on_delete=models.CASCADE,
        related_name="consumptions",
        null=True,
        blank=True,
    )
    # The calendar day the meal was consumed on. Stored as a Date
    # (not a timestamp) so "today" is unambiguous regardless of which
    # timezone the client is in or what hour they ticked.
    consumed_on = models.DateField(default=timezone.now)
    # When the row was actually written — useful for the trainer's
    # activity feed ("Sarah ticked breakfast at 7:42am").
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # Don't allow the same item to be ticked twice on the
            # same day. iOS will get a 200 + the existing row when
            # POSTing a duplicate (idempotency on the server side
            # means the iOS client doesn't have to track which ticks
            # have already been synced).
            models.UniqueConstraint(
                fields=["client", "meal_item", "consumed_on"],
                condition=models.Q(meal_item__isnull=False),
                name="unique_item_consumption_per_day",
            ),
            # And the same meal can only be meal-level ticked once.
            models.UniqueConstraint(
                fields=["client", "meal", "consumed_on"],
                condition=models.Q(meal_item__isnull=True),
                name="unique_meal_consumption_per_day",
            ),
        ]
        indexes = [
            # "What did this client log over the last N days?" — the
            # core query for both the iOS sync endpoint AND the
            # trainer's activity feed on the client detail page.
            models.Index(fields=["client", "-consumed_on"]),
        ]
        ordering = ["-consumed_on", "-created_at"]

    def __str__(self):
        if self.meal_item:
            return f"{self.client.username} ate {self.meal_item.food_name} on {self.consumed_on}"
        return f"{self.client.username} completed {self.meal.title} on {self.consumed_on}"
