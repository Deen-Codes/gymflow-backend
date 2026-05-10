"""DEEN-PLAN — icon_priority on ExerciseCatalog.

Adds a small integer priority used by the icon-production queue. The
first batch (priority=10) is Deen's own PT plan — 30 lifts. Default
0 = default queue.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workouts", "0013_exercise_provenance"),
    ]

    operations = [
        migrations.AddField(
            model_name="exercisecatalog",
            name="icon_priority",
            field=models.PositiveSmallIntegerField(default=0, db_index=True),
        ),
    ]
