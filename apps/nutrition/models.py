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

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class FoodLibraryItem(models.Model):
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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

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
