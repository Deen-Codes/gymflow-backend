"""NUTRITION-V3 — SoloFoodLogEntry.meal_template_item back-reference.

Lets the iOS planned-meal card rebuild its tick state from server
data on re-mount, so ticks survive tab navigation and the user
can't double-log the same item by tapping it twice across
sessions.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nutrition", "0015_solofoodlogentry_from_meal_plan"),
    ]

    operations = [
        migrations.AddField(
            model_name="solofoodlogentry",
            name="meal_template_item",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="solo_log_entries",
                to="nutrition.mealtemplateitem",
            ),
        ),
    ]
