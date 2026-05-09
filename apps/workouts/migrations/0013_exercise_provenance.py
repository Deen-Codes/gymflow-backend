# T1.9 / EDIT-PROVENANCE-TRACKING — where did this Exercise row
# originate? Drives the AI PT context so the model can comment on
# user-made edits during chat / weekly review without asking.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workouts", "0012_exercise_catalog_metadata_expansion"),
    ]

    operations = [
        migrations.AddField(
            model_name="exercise",
            name="provenance",
            field=models.CharField(
                blank=True,
                choices=[
                    ("ai_generated", "AI generated"),
                    ("template",     "Template"),
                    ("user_edit",    "User edit"),
                ],
                default="template",
                max_length=16,
            ),
        ),
    ]
