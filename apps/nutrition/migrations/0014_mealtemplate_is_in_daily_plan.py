"""DAILY-MEAL-PLAN — MealTemplate.is_in_daily_plan flag."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nutrition", "0013_mealtemplate"),
    ]

    operations = [
        migrations.AddField(
            model_name="mealtemplate",
            name="is_in_daily_plan",
            field=models.BooleanField(default=False, db_index=True),
        ),
    ]
