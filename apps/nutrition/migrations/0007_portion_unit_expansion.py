"""Extend FoodLibraryItem.portion_type with named non-gram units
(ml, oz, tbsp, tsp, cup) so trainers can author foods like
"1 tbsp olive oil = 120 kcal" without using the freeform unit slot.

Existing rows continue to work — `grams` and `unit` are unchanged.
This migration only widens the choices on the field.
"""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("nutrition", "0006_meal_consumption"),
    ]

    operations = [
        migrations.AlterField(
            model_name="foodlibraryitem",
            name="portion_type",
            field=models.CharField(
                choices=[
                    ("grams", "Grams"),
                    ("ml",    "Millilitres"),
                    ("oz",    "Ounces"),
                    ("tbsp",  "Tablespoon"),
                    ("tsp",   "Teaspoon"),
                    ("cup",   "Cup"),
                    ("unit",  "Unit (egg, scoop, slice…)"),
                ],
                default="grams",
                max_length=10,
            ),
        ),
    ]
