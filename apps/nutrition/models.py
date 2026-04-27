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

    # Portion mode. Some foods are weighed (rice, chicken) — macros per
    # `reference_grams`. Some are counted (eggs, wraps, protein scoops)
    # — macros per 1 unit. `unit_label` carries the noun ("egg", "wrap").
    PORTION_GRAMS = "grams"
    PORTION_UNIT  = "unit"
    PORTION_CHOICES = [
        (PORTION_GRAMS, "Per gram (weighed)"),
        (PORTION_UNIT,  "Per unit (eggs, wraps, scoops)"),
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
