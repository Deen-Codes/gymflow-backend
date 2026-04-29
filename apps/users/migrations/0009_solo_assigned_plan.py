# SOLO-02 — adds the `assigned_workout_plan` FK on SoloProfile so a
# Solo user can pick a programme from the catalog and the existing
# /api/workouts/plan/active/ endpoint can resolve it without any
# special-casing.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0008_solo_role_and_profile"),
        ("workouts", "0006_solo_template"),
    ]

    operations = [
        migrations.AddField(
            model_name="soloprofile",
            name="assigned_workout_plan",
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="assigned_solo_users",
                to="workouts.workoutplan",
            ),
        ),
    ]
