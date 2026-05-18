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
#   • Afletics-curated               — our own additions (branded items
#                                    we add manually; pro-tier fields)
#
# No external runtime calls. All food data ships in CuratedFood and is
# updated only via the seed/import management commands. Open Food Facts
# was investigated and rejected — its CC BY-SA copyleft would force our
# commercial DB to also be share-alike. Branded items we want for our
# users are added manually via `popular_foods.yaml`.
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
    SOURCE_AFLETICS = "afletics"
    SOURCE_CHOICES = [
        (SOURCE_USDA,   "USDA FoodData Central"),
        (SOURCE_FSA_UK, "UK FSA McCance & Widdowson's"),
        (SOURCE_AUSNUT, "AUSNUT 2011-13"),
        (SOURCE_CIQUAL, "CIQUAL"),
        (SOURCE_AFLETICS, "Afletics curated"),
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

    # FOOD-DB-V2 — portion-unit support. Lets the food picker
    # express "1 egg" / "1 slice" / "1 wrap" / "1 scoop" as a
    # first-class portion alongside grams.
    #
    # Why this matters: people don't weigh slices of bread or
    # wraps. The "1 unit" affordance is what makes the picker
    # actually usable for everyday foods. Macros stay stored
    # per-100g (so the math is uniform); `unit_grams` carries the
    # gram-equivalent of one unit, and the iOS picker uses it to
    # render unit-based stepper rows.
    #
    # When `portion_unit == "grams"` (the default for raw / bulk
    # foods like rice, oil, yogurt), `unit_grams` is unused and
    # the picker shows 50g / 100g / 150g / 200g chips.
    #
    # When `portion_unit != "grams"` (e.g. an egg), the picker
    # shows 1 / 2 / 3 / 4 chips and computes
    # `kcal = kcal_per_100g * unit_grams * N / 100`.
    PORTION_GRAMS = "grams"
    PORTION_ML    = "ml"
    PORTION_PIECE = "piece"   # e.g. 1 banana, 1 apple
    PORTION_SLICE = "slice"   # e.g. 1 slice of bread, 1 slice of cheese
    PORTION_WRAP  = "wrap"
    PORTION_SCOOP = "scoop"
    PORTION_TBSP  = "tbsp"
    PORTION_TSP   = "tsp"
    PORTION_CUP   = "cup"
    PORTION_OZ    = "oz"
    PORTION_EGG   = "egg"
    PORTION_BAR   = "bar"     # protein bars, chocolate bars
    PORTION_CAN   = "can"     # canned drinks
    PORTION_BOTTLE = "bottle"
    PORTION_PACK  = "pack"    # packets / sachets / pots
    PORTION_PINT  = "pint"    # beer, milk in pubs
    PORTION_SHOT  = "shot"    # 25 ml spirit measure
    PORTION_MEAL  = "meal"    # whole prepared meals — Big Mac meal,
                              # Nando's quarter chicken meal, Sunday
                              # roast. The "meal" unit means the
                              # complete plate as the chain serves it,
                              # macros bundled.
    PORTION_CHOICES = [
        (PORTION_GRAMS,  "Grams"),
        (PORTION_ML,     "Millilitres"),
        (PORTION_PIECE,  "Piece"),
        (PORTION_SLICE,  "Slice"),
        (PORTION_WRAP,   "Wrap"),
        (PORTION_SCOOP,  "Scoop"),
        (PORTION_TBSP,   "Tablespoon"),
        (PORTION_TSP,    "Teaspoon"),
        (PORTION_CUP,    "Cup"),
        (PORTION_OZ,     "Ounce"),
        (PORTION_EGG,    "Egg"),
        (PORTION_BAR,    "Bar"),
        (PORTION_CAN,    "Can"),
        (PORTION_BOTTLE, "Bottle"),
        (PORTION_PACK,   "Pack"),
        (PORTION_PINT,   "Pint"),
        (PORTION_SHOT,   "Shot"),
        (PORTION_MEAL,   "Meal"),
    ]
    portion_unit = models.CharField(
        max_length=10,
        choices=PORTION_CHOICES,
        default=PORTION_GRAMS,
        db_index=True,
    )
    # Gram-equivalent of one unit. Required when portion_unit isn't
    # "grams". For "1 large egg" → 50.0; "1 slice white bread" → 30.0;
    # "1 chicken wrap" → 220.0.
    unit_grams = models.FloatField(null=True, blank=True)

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
    (e.g. "100g of brown rice = 360 kcal / 7p / 75c / 3f"). Optional
    `source`, `external_id`, `brand` so the same row can either be a
    custom item the trainer typed in OR a snapshot of a CuratedFood
    catalog row they pulled from the food picker.

    Pre-NUTRITION-DB rows have `source="off"` (Open Food Facts snapshots
    from when we proxied that DB). Those rows still work; new snapshots
    use `source="afletics"`.
    """

    SOURCE_CUSTOM   = "custom"
    SOURCE_AFLETICS  = "afletics"
    # Legacy — pre-NUTRITION-DB rows snapshotted from Open Food Facts.
    # No new rows use this; kept in choices so existing data still
    # validates in admin.
    SOURCE_OFF      = "off"
    SOURCE_CHOICES = [
        (SOURCE_CUSTOM,  "Custom"),
        (SOURCE_AFLETICS, "Afletics catalog"),
        (SOURCE_OFF,     "Open Food Facts (legacy)"),
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

    # NUTRITION-V3 — true for entries written by the planned-meal
    # tick path in `SoloPlannedMealCard`. The macro hero still sums
    # them (so ticking a planned item updates kcal/P/C/F live), but
    # the LOG list filters them out — the LOG is meant for
    # "extra food outside meals", not the items the user already
    # planned for.
    from_meal_plan = models.BooleanField(default=False, db_index=True)

    # NUTRITION-V3 — back-reference to the MealTemplateItem the user
    # ticked. Populated alongside from_meal_plan=true. Lets the iOS
    # card rebuild its tick map from server state on re-mount /
    # tab re-entry, so ticks survive navigation and the user can't
    # accidentally double-log the same item by tapping it twice
    # across two sessions. SET_NULL on item delete so the historical
    # log row survives meal-template edits.
    meal_template_item = models.ForeignKey(
        "MealTemplateItem",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="solo_log_entries",
    )

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


# ====================================================================
# T1.7 — NutritionTemplate
#
# Free-tier "Tier 2" path per DISPATCH_BRIEF.md: a curated set of
# nutrition plans that get scaled deterministically (no AI cost) by
# user weight + goal. Replaces the "AI build is the only way to get
# a plan" coupling, so free users hit a real plan day one.
#
# Each row is a goal-aligned macro split with a tagline + descriptive
# rationale. The recommend endpoint (T1.8) ranks them by goal match
# and returns the top 3. iOS surfaces them as the "Browse templates"
# carousel on the nutrition empty state (T2.6).
#
# Sample meal slots are deferred to the AI-MEAL-PLAN-V2 work (#226)
# — the first cut of templates ships with macro splits + scaling
# rules only, which is enough for the recommend endpoint to be
# useful and for free users to see a real targets-set plan without
# a NULL data layer.
# ====================================================================
class NutritionTemplate(models.Model):
    """Curated free-tier nutrition plan template.

    Macro split is expressed as protein g/kg + fat g/kg + a calorie
    delta off TDEE. Carbs are computed as the kcal remainder so the
    template auto-scales to any user's bodyweight without storing
    per-weight rows.
    """

    # Goal alignment — used by the recommend endpoint to rank the
    # templates against the user's `goals` array. Multi-tag (a
    # single template can fit multiple goals; lean_bulk fits both
    # build_muscle and stay_consistent for example).
    GOAL_LOSE_FAT       = "lose_fat"
    GOAL_BUILD_MUSCLE   = "build_muscle"
    GOAL_GET_STRONGER   = "get_stronger"
    GOAL_STAY_CONSISTENT = "stay_consistent"
    GOAL_TRAIN_FOR_SPORT = "train_for_sport"

    slug        = models.SlugField(max_length=64, unique=True, db_index=True)
    name        = models.CharField(max_length=80)
    tagline     = models.CharField(max_length=160, blank=True)
    summary     = models.TextField(blank=True)

    # Macro scaling rules. `protein_g_per_kg` * bodyweight_kg →
    # protein target. `fat_g_per_kg` * bodyweight_kg → fat target.
    # `kcal_delta_vs_tdee` is added to TDEE (use negative for cuts).
    # Carbs computed as remainder.
    protein_g_per_kg     = models.FloatField(default=1.8)
    fat_g_per_kg         = models.FloatField(default=0.8)
    kcal_delta_vs_tdee   = models.IntegerField(default=0)

    # Goal alignment array. Stored as comma-separated lowercase
    # tokens so we don't pin to Postgres (matches CuratedFood's
    # region_codes pattern).
    goal_alignment       = models.CharField(max_length=128, blank=True, default="")

    # Dietary pattern compatibility — which onboarded users this
    # template is shown to. Empty == all. Comma-separated values
    # matching SoloProfile.DIETARY_* tokens.
    dietary_compatibility = models.CharField(max_length=128, blank=True, default="")

    # Pace expectation — used by the iOS carousel preview as a
    # one-line "what this looks like" subtitle (e.g. "~0.5 kg/week
    # cut", "lean bulk, ~0.25 kg/week up").
    pace_label  = models.CharField(max_length=80, blank=True, default="")

    # Display order in the carousel (lower = earlier). Same template
    # may rank differently per goal once T1.8 lands; this is the
    # baseline tiebreaker.
    sort_order  = models.PositiveSmallIntegerField(default=100)

    is_published = models.BooleanField(default=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return f"{self.name} ({self.slug})"

    # ----------------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------------
    def goal_tags(self) -> list[str]:
        return [t for t in (self.goal_alignment or "").split(",") if t]

    def dietary_tags(self) -> list[str]:
        return [t for t in (self.dietary_compatibility or "").split(",") if t]

    def scaled_macros(self, bodyweight_kg: float | None,
                      tdee_kcal: int | None) -> dict[str, int]:
        """Compute scaled macros for a specific user.

        Falls back to a 75 kg / 2400 kcal default user when inputs
        aren't set so the carousel always renders something
        plausible. The caller (recommend endpoint / iOS preview)
        can override.
        """
        bw    = bodyweight_kg or 75.0
        tdee  = tdee_kcal or int(bw * 30.0)   # 30 kcal/kg default
        kcal  = max(1200, tdee + (self.kcal_delta_vs_tdee or 0))
        protein_g = round(bw * (self.protein_g_per_kg or 1.8))
        fat_g     = round(bw * (self.fat_g_per_kg or 0.8))
        used_kcal = (protein_g * 4) + (fat_g * 9)
        carb_kcal = max(0, kcal - used_kcal)
        carbs_g   = max(0, round(carb_kcal / 4))
        return {
            "calories": int(kcal),
            "protein":  int(protein_g),
            "carbs":    int(carbs_g),
            "fats":     int(fat_g),
        }


# ====================================================================
# T2.9 — MealTemplate
#
# User-authored or AI-suggested meal that the user can save as a
# reusable favourite ("my usual breakfast") and one-tap log to the
# food diary later. Each MealTemplate has a list of MealTemplateItem
# rows pointing at CuratedFood with a portion_g.
#
# Endpoints (apps/nutrition/meal_template_views.py):
#   • GET  /api/nutrition/meal-templates/
#   • POST /api/nutrition/meal-templates/
#   • PATCH /api/nutrition/meal-templates/<id>/
#   • DELETE /api/nutrition/meal-templates/<id>/
#   • POST /api/nutrition/meal-templates/<id>/log/   ← one-tap log
#
# When the user logs a template, each item becomes a SoloFoodLogEntry
# row with the food FK populated + a derived `name`/macros snapshot.
# ====================================================================
class MealTemplate(models.Model):
    SLOT_BREAKFAST     = "breakfast"
    SLOT_LUNCH         = "lunch"
    SLOT_DINNER        = "dinner"
    SLOT_SNACK         = "snack"
    SLOT_PRE_WORKOUT   = "pre_workout"
    SLOT_INTRA_WORKOUT = "intra_workout"
    SLOT_POST_WORKOUT  = "post_workout"
    SLOT_CHOICES = [
        (SLOT_BREAKFAST,     "Breakfast"),
        (SLOT_LUNCH,         "Lunch"),
        (SLOT_DINNER,        "Dinner"),
        (SLOT_SNACK,         "Snack"),
        (SLOT_PRE_WORKOUT,   "Pre-workout"),
        (SLOT_INTRA_WORKOUT, "Intra-workout"),
        (SLOT_POST_WORKOUT,  "Post-workout"),
    ]

    SOURCE_USER = "user_edit"
    SOURCE_AI   = "ai_generated"
    SOURCE_CHOICES = [
        (SOURCE_USER, "User-built"),
        (SOURCE_AI,   "AI-generated"),
    ]

    user      = models.ForeignKey(
        "users.User", on_delete=models.CASCADE, related_name="meal_templates",
    )
    title     = models.CharField(max_length=120)
    slot      = models.CharField(max_length=20, choices=SLOT_CHOICES, default=SLOT_BREAKFAST, db_index=True)
    notes     = models.CharField(max_length=240, blank=True)
    source    = models.CharField(max_length=16, choices=SOURCE_CHOICES, default=SOURCE_USER)
    is_favourite = models.BooleanField(default=True)
    # DAILY-MEAL-PLAN — when SoloProfile.nutrition_mode is "meal_plan",
    # MealTemplate rows flagged is_in_daily_plan=True are surfaced as
    # the user's set daily plan. The same set shows every day with
    # one-tap "Log" buttons. Toggling this flag is how the user
    # builds / removes meals from their plan.
    is_in_daily_plan = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_favourite", "-updated_at"]
        indexes = [
            models.Index(fields=["user", "slot"]),
        ]

    def __str__(self):
        return f"{self.user_id}/{self.title} ({self.slot})"

    def totals(self) -> dict:
        """Sum macros across all items. Computed live so item edits
        don't need a denormalised cache field."""
        kcal = 0.0; p = 0.0; c = 0.0; f = 0.0
        for it in self.items.all().select_related("food"):
            scale = (it.portion_g or 0.0) / 100.0
            food = it.food
            if food is None:
                continue
            kcal += food.kcal_per_100g    * scale
            p    += food.protein_per_100g * scale
            c    += food.carbs_per_100g   * scale
            f    += food.fat_per_100g     * scale
        return {
            "calories": round(kcal, 1),
            "protein":  round(p, 1),
            "carbs":    round(c, 1),
            "fats":     round(f, 1),
        }


class MealTemplateItem(models.Model):
    template  = models.ForeignKey(
        MealTemplate, on_delete=models.CASCADE, related_name="items",
    )
    food      = models.ForeignKey(
        CuratedFood, on_delete=models.PROTECT, related_name="meal_template_items",
    )
    portion_g = models.FloatField()
    order     = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.template_id}/{self.food.name} @ {self.portion_g}g"
