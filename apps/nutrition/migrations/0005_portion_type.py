"""Add portion_type / unit_label / units for unit-based foods.

Some foods are weighed in grams (chicken, rice). Others are counted
in units (eggs, wraps, protein scoops). This migration adds the
opt-in fields without touching existing rows — defaults preserve
the previous "everything is grams" behaviour.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nutrition", "0004_nutritionplan_created_at"),
    ]

    operations = [
        # FoodLibraryItem -------------------------------------------------
        migrations.AddField(
            model_name="foodlibraryitem",
            name="portion_type",
            field=models.CharField(
                max_length=10,
                choices=[("grams", "Per gram (weighed)"),
                         ("unit",  "Per unit (eggs, wraps, scoops)")],
                default="grams",
            ),
        ),
        migrations.AddField(
            model_name="foodlibraryitem",
            name="unit_label",
            field=models.CharField(max_length=40, blank=True, default=""),
        ),

        # NutritionMealItem -----------------------------------------------
        migrations.AddField(
            model_name="nutritionmealitem",
            name="portion_type",
            field=models.CharField(
                max_length=10,
                choices=[("grams", "Per gram (weighed)"),
                         ("unit",  "Per unit (eggs, wraps, scoops)")],
                default="grams",
            ),
        ),
        migrations.AddField(
            model_name="nutritionmealitem",
            name="unit_label",
            field=models.CharField(max_length=40, blank=True, default=""),
        ),
        migrations.AddField(
            model_name="nutritionmealitem",
            name="units",
            field=models.FloatField(default=0),
        ),
    ]
