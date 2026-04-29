# SOLO-02 — adds the catalog flags to WorkoutPlan so a single row can
# be marked as a public solo template, with a JSON meta blob that
# powers catalog filtering.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workouts", "0005_workoutplan_created_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="workoutplan",
            name="is_solo_template",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="workoutplan",
            name="programme_meta",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
