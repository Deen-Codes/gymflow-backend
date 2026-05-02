# R7-2 (#59) — post-session "how did that feel?" feedback fields.
#
# rpe: 1–10 perceived exertion; mood: short categorical label
# ("good"/"fine"/"off"/"tough" today, schemaless to allow evolution).
# Both nullable/blank so older clients that PATCH only `notes` keep
# working and existing rows stay valid.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workouts", "0009_workoutsession_notes"),
    ]

    operations = [
        migrations.AddField(
            model_name="workoutsession",
            name="rpe",
            field=models.SmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="workoutsession",
            name="mood",
            field=models.CharField(blank=True, default="", max_length=16),
        ),
    ]
