# NUTRITION-AI-ONBOARDING — captured during the cinematic nutrition
# setup flow that mirrors AI-BUILD-ONBOARDING on the workout side.
# All fields default to empty / unspecified sentinels so existing
# users aren't broken.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0012_ai_build_onboarding"),
    ]

    operations = [
        migrations.AddField(
            model_name="soloprofile",
            name="dietary_pattern",
            field=models.CharField(
                max_length=16,
                choices=[
                    ("none",        "None"),
                    ("pescatarian", "Pescatarian"),
                    ("vegetarian",  "Vegetarian"),
                    ("vegan",       "Vegan"),
                    ("halal",       "Halal"),
                    ("kosher",      "Kosher"),
                    ("other",       "Other"),
                ],
                blank=True,
                default="",
            ),
        ),
        migrations.AddField(
            model_name="soloprofile",
            name="dietary_other",
            field=models.CharField(max_length=120, blank=True, default=""),
        ),
        migrations.AddField(
            model_name="soloprofile",
            name="food_restrictions",
            field=models.JSONField(default=list, blank=True),
        ),
        migrations.AddField(
            model_name="soloprofile",
            name="food_dislikes",
            field=models.JSONField(default=list, blank=True),
        ),
        migrations.AddField(
            model_name="soloprofile",
            name="meals_per_day",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="soloprofile",
            name="cooking_comfort",
            field=models.CharField(
                max_length=16,
                choices=[
                    ("love",         "Love cooking"),
                    ("comfortable",  "Comfortable"),
                    ("preassembled", "Mostly pre-assembled"),
                    ("eating_out",   "Eat out a lot"),
                ],
                blank=True,
                default="",
            ),
        ),
    ]
