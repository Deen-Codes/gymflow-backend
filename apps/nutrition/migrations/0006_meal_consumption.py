"""Phase C.2 — server-side meal consumption tracking.

Adds NutritionMealConsumption to replace the iOS-local
UserDefaults-backed MealConsumptionStore. See model docstring in
apps/nutrition/models.py for design notes.
"""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
from django.utils import timezone


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("nutrition", "0005_portion_type"),
    ]

    operations = [
        migrations.CreateModel(
            name="NutritionMealConsumption",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("consumed_on", models.DateField(default=timezone.now)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("client", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="meal_consumptions",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("meal", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="consumptions",
                    to="nutrition.nutritionmeal",
                )),
                ("meal_item", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="consumptions",
                    to="nutrition.nutritionmealitem",
                )),
            ],
            options={
                "ordering": ["-consumed_on", "-created_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="nutritionmealconsumption",
            constraint=models.UniqueConstraint(
                fields=("client", "meal_item", "consumed_on"),
                condition=models.Q(("meal_item__isnull", False)),
                name="unique_item_consumption_per_day",
            ),
        ),
        migrations.AddConstraint(
            model_name="nutritionmealconsumption",
            constraint=models.UniqueConstraint(
                fields=("client", "meal", "consumed_on"),
                condition=models.Q(("meal_item__isnull", True)),
                name="unique_meal_consumption_per_day",
            ),
        ),
        migrations.AddIndex(
            model_name="nutritionmealconsumption",
            index=models.Index(
                fields=["client", "-consumed_on"],
                name="nutrition_n_client_recent_idx",
            ),
        ),
    ]
