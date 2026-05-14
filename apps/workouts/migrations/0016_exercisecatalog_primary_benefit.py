"""Adds ExerciseCatalog.primary_benefit — short "why this lift?" copy.

EXERCISE-COPY-WHY (May 2026, Deen QC) — the enlarged exercise view
already shows form_description (how to do it) and common_mistakes
(what not to do), but had no "why is this exercise worth doing?"
section. iOS users opening Cable Crossover saw setup instructions
without context on what the movement actually develops. The new
field carries one short paragraph in coach voice answering exactly
that question.

Empty by default — populated via seed_exercise_form_copy from the
staples YAMLs and (later) by an AI backfill pass for the long tail.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workouts", "0015_adhoc_session_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="exercisecatalog",
            name="primary_benefit",
            field=models.TextField(blank=True),
        ),
    ]
