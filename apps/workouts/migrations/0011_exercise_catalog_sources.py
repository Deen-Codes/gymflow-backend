# EXERCISE-DB — extend ExerciseCatalog.source choices to cover
# the new ingest sources (Free Exercise DB + Afletics curated).
# Choice expansion is a no-op at the schema level (CharField) but
# we record the migration so the choices list stays in sync with
# the model definition for future migrations.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workouts", "0010_workoutsession_rpe_mood"),
    ]

    operations = [
        migrations.AlterField(
            model_name="exercisecatalog",
            name="source",
            field=models.CharField(
                choices=[
                    ("curated",          "Curated"),
                    ("wger",             "wger"),
                    ("free_exercise_db", "Free Exercise DB"),
                    ("afletics",           "Afletics curated"),
                ],
                default="curated",
                max_length=16,
            ),
        ),
    ]
