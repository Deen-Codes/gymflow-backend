"""ONBOARDING-QUICK-START — per-step completion flags on SoloProfile.

Five new booleans drive the in-app setup strip's progress bar:
  - setup_apple_health_done
  - setup_body_stats_done
  - setup_goal_done
  - setup_training_done
  - setup_nutrition_style_done

Backfill data migration: for every existing SoloProfile, set each
flag to True if the underlying fields look filled. So users who'd
already set their weight, dietary pattern, etc. before the strip
shipped don't get a hub asking them to "set up" things they've
already done.

Mapping:
  body_stats     → height_cm not null AND bodyweight_kg not null
  goal           → goals list non-empty OR goal_weight_kg not null
  training       → experience non-empty
  nutrition_style→ dietary_pattern non-empty
  apple_health   → conservative — left False; the user grants this
                   each session anyway, can't infer from server.
"""
from django.db import migrations, models


def backfill_done_flags(apps, schema_editor):
    SoloProfile = apps.get_model("users", "SoloProfile")
    for profile in SoloProfile.objects.iterator(chunk_size=200):
        # Body stats — height + bodyweight both required.
        if profile.height_cm and profile.bodyweight_kg:
            profile.setup_body_stats_done = True

        # Goal — either a goals list or a goal_weight is enough.
        # `goals` is a JSONField default=list; treat empty list as
        # "not done", any non-empty list as done.
        # Some installs may have goal_weight_kg on the parent User
        # rather than SoloProfile — we check both safely.
        has_goal_list = bool(profile.goals)
        has_goal_weight = bool(getattr(profile, "goal_weight_kg", None))
        if has_goal_list or has_goal_weight:
            profile.setup_goal_done = True

        # Training experience — explicit non-empty string.
        if (profile.experience or "").strip():
            profile.setup_training_done = True

        # Nutrition style — dietary pattern explicitly chosen.
        if (profile.dietary_pattern or "").strip():
            profile.setup_nutrition_style_done = True

        profile.save(update_fields=[
            "setup_body_stats_done",
            "setup_goal_done",
            "setup_training_done",
            "setup_nutrition_style_done",
        ])


def noop_reverse(apps, schema_editor):
    """No-op on rollback — leaving flags as set is harmless."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0018_soloprofile_nutrition_mode"),
    ]

    operations = [
        migrations.AddField(
            model_name="soloprofile",
            name="setup_apple_health_done",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="soloprofile",
            name="setup_body_stats_done",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="soloprofile",
            name="setup_goal_done",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="soloprofile",
            name="setup_training_done",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="soloprofile",
            name="setup_nutrition_style_done",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(backfill_done_flags, noop_reverse),
    ]
