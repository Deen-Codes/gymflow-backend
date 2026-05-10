"""ONBOARDING-QUICK-START — seed the `set_up_strong` trophy.

Awarded once all five setup-strip steps are marked done on the
user's SoloProfile. Joins the existing catalogue with a new
`onboarding` category — the AlterField below registers that
choice with Django's migration state so subsequent
`makemigrations` runs don't generate a noisy no-op for it.
"""
from django.db import migrations, models


CATEGORY_CHOICES = [
    ("workout_volume", "Workout Volume"),
    ("streaks",        "Streaks"),
    ("frequency",      "Frequency"),
    ("personal_record", "Personal Records"),
    ("reps_sets",      "Reps & Sets"),
    ("time_special",   "Time & Special Days"),
    ("check_ins",      "Check-ins & Progress"),
    ("nutrition",      "Nutrition & Hydration"),
    ("body",           "Body Composition"),
    ("onboarding",     "Onboarding"),
]


def seed_set_up_strong(apps, schema_editor):
    Trophy = apps.get_model("trophies", "Trophy")
    Trophy.objects.update_or_create(
        code="set_up_strong",
        defaults={
            "name":        "Set Up Strong",
            "description": "Completed your profile setup. The AI knows you now.",
            "category":    "onboarding",
            "rarity":      "common",
            "icon":        "checkmark.seal.fill",
            "sort_order":  10,
        },
    )


def remove_set_up_strong(apps, schema_editor):
    Trophy = apps.get_model("trophies", "Trophy")
    Trophy.objects.filter(code="set_up_strong").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("trophies", "0002_better_icons"),
    ]

    operations = [
        migrations.AlterField(
            model_name="trophy",
            name="category",
            field=models.CharField(
                max_length=40,
                choices=CATEGORY_CHOICES,
            ),
        ),
        migrations.RunPython(seed_set_up_strong, reverse_code=remove_set_up_strong),
    ]
