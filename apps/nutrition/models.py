from django.conf import settings
from django.db import models


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

    calories = models.FloatField(default=0)
    protein = models.FloatField(default=0)
    carbs = models.FloatField(default=0)
    fats = models.FloatField(default=0)

    order = models.IntegerField(default=1)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.meal.title} - {self.food_name}"
