from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workouts", "0004_exercisecatalog_and_library_metadata"),
    ]

    operations = [
        # nullable + auto_now_add so the column adds cleanly on existing
        # rows (they get NULL; new rows get the current timestamp).
        migrations.AddField(
            model_name="workoutplan",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, null=True),
        ),
    ]
