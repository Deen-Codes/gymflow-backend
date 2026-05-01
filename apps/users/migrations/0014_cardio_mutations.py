# CARDIO-MUTATIONS — AI-proposed cardio plan changes. Mirrors the
# WorkoutMutation / NutritionMutation tables. Same audit shape:
# kind / status / original_value / new_value / ai_rationale /
# timestamps / chat_turn_ref. Indexes on (user, status) and
# (user, -proposed_at) match the existing two tables.

from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0013_nutrition_ai_onboarding"),
    ]

    operations = [
        migrations.CreateModel(
            name="CardioMutation",
            fields=[
                ("id", models.AutoField(
                    auto_created=True, primary_key=True,
                    serialize=False, verbose_name="ID",
                )),
                ("kind", models.CharField(
                    max_length=24,
                    choices=[
                        ("assign_session_type", "Assign session type"),
                        ("adjust_volume",       "Adjust volume"),
                        ("swap_modality",       "Swap modality"),
                        ("change_priority",     "Change priority"),
                    ],
                )),
                ("status", models.CharField(
                    max_length=10,
                    choices=[
                        ("proposed", "Proposed"),
                        ("applied",  "Applied"),
                        ("declined", "Declined"),
                        ("expired",  "Expired"),
                    ],
                    default="proposed",
                )),
                ("original_value", models.JSONField(default=dict, blank=True)),
                ("new_value",      models.JSONField(default=dict, blank=True)),
                ("ai_rationale",   models.TextField(blank=True, default="")),
                ("proposed_at",    models.DateTimeField(auto_now_add=True)),
                ("decided_at",     models.DateTimeField(null=True, blank=True)),
                ("applied_at",     models.DateTimeField(null=True, blank=True)),
                ("chat_turn_ref",  models.CharField(max_length=64, blank=True, default="")),
                ("user", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="cardio_mutations",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"ordering": ["-proposed_at"]},
        ),
        migrations.AddIndex(
            model_name="cardiomutation",
            index=models.Index(
                fields=["user", "status"],
                name="users_cardio_user_status_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="cardiomutation",
            index=models.Index(
                fields=["user", "-proposed_at"],
                name="users_cardio_user_propose_idx",
            ),
        ),
    ]
