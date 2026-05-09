# T2.10 — RecentEditLog. Tracks last 50 user-side edits per user so
# the AI PT context can comment on what the user changed since the
# AI build / template assign.
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0016_solo_target_defaults_zero"),
    ]

    operations = [
        migrations.CreateModel(
            name="RecentEditLog",
            fields=[
                ("id", models.AutoField(
                    auto_created=True, primary_key=True, serialize=False,
                    verbose_name="ID",
                )),
                ("kind", models.CharField(
                    max_length=24,
                    choices=[
                        ("workout_swap",    "Workout: swap exercise"),
                        ("workout_set",     "Workout: change sets"),
                        ("workout_reps",    "Workout: change reps"),
                        ("workout_rest",    "Workout: change rest"),
                        ("workout_add",     "Workout: add exercise"),
                        ("workout_remove",  "Workout: remove exercise"),
                        ("nutrition_meal",  "Nutrition: edit meal"),
                        ("nutrition_macro", "Nutrition: edit macros"),
                        ("other",           "Other"),
                    ],
                )),
                ("summary", models.CharField(max_length=240)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("user", models.ForeignKey(
                    to=settings.AUTH_USER_MODEL,
                    on_delete=models.CASCADE,
                    related_name="recent_edits",
                )),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["user", "-created_at"], name="users_recen_user_id_2c5410_idx"),
                ],
            },
        ),
    ]
