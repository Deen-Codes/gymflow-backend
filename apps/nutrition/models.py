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
