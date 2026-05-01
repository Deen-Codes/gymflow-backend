# Phase A — AI-driven mutation tool surface.
#
# Adds:
#   • SoloProfile.phase (cut / maintenance / bulk)
#   • SoloProfile.phase_started_at
#   • WorkoutMutation table (audit trail for proposed + applied
#     workout-plan changes)
#   • NutritionMutation table (audit trail for proposed + applied
#     nutrition changes)
#
# Why one migration covers all four: they ship together. The phase
# field is meaningless without the change_goal_phase mutation type;
# the mutation tables are scaffolding without the phase field. One
# atomic migration keeps the deploy story clean.

from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0010_solo_macro_targets"),
    ]

    operations = [
        # ----------------------------------------------------------
        # SoloProfile — phase, phase_started_at, goal_weight_kg.
        # All three are AI-context fields. Phase + start drive the
        # longitudinal coaching prompt; goal_weight_kg is what every
        # observation gets framed against ("X kg to goal" rather
        # than "X kg lost").
        # ----------------------------------------------------------
        migrations.AddField(
            model_name="soloprofile",
            name="phase",
            field=models.CharField(
                max_length=12,
                choices=[
                    ("cut",         "Cut"),
                    ("maintenance", "Maintenance"),
                    ("bulk",        "Bulk"),
                ],
                default="maintenance",
            ),
        ),
        migrations.AddField(
            model_name="soloprofile",
            name="phase_started_at",
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="soloprofile",
            name="goal_weight_kg",
            field=models.FloatField(null=True, blank=True),
        ),

        # ----------------------------------------------------------
        # WorkoutMutation
        # ----------------------------------------------------------
        migrations.CreateModel(
            name="WorkoutMutation",
            fields=[
                ("id", models.AutoField(
                    auto_created=True, primary_key=True,
                    serialize=False, verbose_name="ID",
                )),
                ("kind", models.CharField(
                    max_length=24,
                    choices=[
                        ("swap_exercise",     "Swap exercise"),
                        ("change_set_scheme", "Change set scheme"),
                        ("reorder_days",      "Reorder days"),
                        ("deload_week",       "Deload week"),
                        ("add_day",           "Add day"),
                        ("remove_day",        "Remove day"),
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
                    related_name="workout_mutations",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "ordering": ["-proposed_at"],
            },
        ),
        migrations.AddIndex(
            model_name="workoutmutation",
            index=models.Index(
                fields=["user", "status"],
                name="users_workou_user_id_status_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="workoutmutation",
            index=models.Index(
                fields=["user", "-proposed_at"],
                name="users_workou_user_id_propose_idx",
            ),
        ),

        # ----------------------------------------------------------
        # NutritionMutation
        # ----------------------------------------------------------
        migrations.CreateModel(
            name="NutritionMutation",
            fields=[
                ("id", models.AutoField(
                    auto_created=True, primary_key=True,
                    serialize=False, verbose_name="ID",
                )),
                ("kind", models.CharField(
                    max_length=24,
                    choices=[
                        ("adjust_macros",     "Adjust macros"),
                        ("swap_preference",   "Swap preference"),
                        ("change_meal_freq",  "Change meal frequency"),
                        # change_goal_phase deliberately uses the
                        # broader char width so we can extend kinds
                        # without an alter_field migration.
                        ("change_goal_phase", "Change goal phase"),
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
                    related_name="nutrition_mutations",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "ordering": ["-proposed_at"],
            },
        ),
        migrations.AddIndex(
            model_name="nutritionmutation",
            index=models.Index(
                fields=["user", "status"],
                name="users_nutrit_user_id_status_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="nutritionmutation",
            index=models.Index(
                fields=["user", "-proposed_at"],
                name="users_nutrit_user_id_propose_idx",
            ),
        ),
    ]
