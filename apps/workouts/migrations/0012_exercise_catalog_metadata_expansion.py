"""EXERCISE-LIB-1500 (#210) — expand ExerciseCatalog metadata.

Adds the fields Free Exercise DB ships (level / mechanic / force /
category / secondary_muscles) so the import lands losslessly, plus
the curated fields Deen specced (form_description / common_mistakes
/ breathing_cues) that surface in the enlarged exercise view.

All fields are nullable / blank-default so the migration is safe on
the existing 43 curated rows — they'll keep working with empty
metadata until back-filled.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workouts", "0011_exercise_catalog_sources"),
    ]

    operations = [
        migrations.AddField(
            model_name="exercisecatalog",
            name="secondary_muscles",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="exercisecatalog",
            name="level",
            field=models.CharField(
                blank=True,
                choices=[
                    ("beginner", "Beginner"),
                    ("intermediate", "Intermediate"),
                    ("expert", "Expert / Advanced"),
                ],
                db_index=True,
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="exercisecatalog",
            name="mechanic",
            field=models.CharField(
                blank=True,
                choices=[
                    ("compound", "Compound"),
                    ("isolation", "Isolation"),
                ],
                db_index=True,
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="exercisecatalog",
            name="force",
            field=models.CharField(
                blank=True,
                choices=[
                    ("push", "Push"),
                    ("pull", "Pull"),
                    ("static", "Static"),
                ],
                db_index=True,
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="exercisecatalog",
            name="category",
            field=models.CharField(
                blank=True,
                choices=[
                    ("strength", "Strength"),
                    ("stretching", "Stretching / Mobility"),
                    ("plyometrics", "Plyometrics"),
                    ("powerlifting", "Powerlifting"),
                    ("cardio", "Cardio"),
                    ("olympic_weightlifting", "Olympic Weightlifting"),
                    ("strongman", "Strongman"),
                ],
                db_index=True,
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="exercisecatalog",
            name="form_description",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="exercisecatalog",
            name="common_mistakes",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="exercisecatalog",
            name="breathing_cues",
            field=models.TextField(blank=True),
        ),
    ]
