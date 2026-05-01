# REST-ASSIGNABLE — per-exercise rest_seconds. The active workout
# previously used a hardcoded 90s rest. Now exercises carry their
# own rest. Trainers set it during programme edit; AI PT mutates
# it via the change_set_scheme tool.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workouts", "0007_exercise_animation_foundation"),
    ]

    operations = [
        migrations.AddField(
            model_name="exercise",
            name="rest_seconds",
            field=models.PositiveSmallIntegerField(default=90),
        ),
    ]
