"""DAILY-MEAL-PLAN — SoloProfile.nutrition_mode."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0017_recenteditlog"),
    ]

    operations = [
        migrations.AddField(
            model_name="soloprofile",
            name="nutrition_mode",
            field=models.CharField(
                max_length=16,
                choices=[
                    ("ad_hoc",    "Eat as you go"),
                    ("meal_plan", "Set meal plan"),
                ],
                default="ad_hoc",
            ),
        ),
    ]
