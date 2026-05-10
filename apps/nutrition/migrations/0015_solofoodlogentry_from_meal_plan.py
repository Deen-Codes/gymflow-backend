"""NUTRITION-V3 — SoloFoodLogEntry.from_meal_plan flag.

True for entries written by the planned-meal tick path
(`SoloPlannedMealCard`). Lets the iOS LOG list filter them out
(LOG is for "extra food", not items the user already planned)
while the macro hero still sums them via the aggregated `eaten`
totals returned by /solo/today/.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nutrition", "0014_mealtemplate_is_in_daily_plan"),
    ]

    operations = [
        migrations.AddField(
            model_name="solofoodlogentry",
            name="from_meal_plan",
            field=models.BooleanField(default=False, db_index=True),
        ),
    ]
