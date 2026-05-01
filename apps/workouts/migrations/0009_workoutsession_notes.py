# WORKOUT-NOTES-POSTSESSION — optional free-text note captured
# in the post-cinematic prompt. Surfaces back to the AI PT in
# _build_user_context as "Last session note: ...".

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workouts", "0008_exercise_rest_seconds"),
    ]

    operations = [
        migrations.AddField(
            model_name="workoutsession",
            name="notes",
            field=models.TextField(blank=True, default=""),
        ),
    ]
