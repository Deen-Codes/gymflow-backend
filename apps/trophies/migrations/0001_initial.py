"""Initial schema + catalogue seed.

Two operations:
  1. CreateModel for Trophy + ClientTrophyAward.
  2. Data migration that upserts the 100 entries from
     `apps.trophies.seed.TROPHY_CATALOGUE`. Idempotent so re-running
     the migration (e.g. after restoring from a backup) does not
     duplicate rows.

The data step also asserts evaluator/catalogue parity — if anyone
adds a new trophy to the seed without a matching evaluator (or vice
versa) the migration fails loudly rather than shipping a half-wired
trophy.
"""
from django.conf import settings
from django.db import migrations, models


def seed_trophies(apps, schema_editor):
    # Verify the seed data and evaluator dict are in lockstep before
    # touching the DB. Imported lazily so the migration module loads
    # cleanly even before the app is installed.
    from apps.trophies.seed import TROPHY_CATALOGUE, assert_codes_unique
    from apps.trophies.evaluators import assert_evaluators_match_catalogue

    assert_codes_unique()
    assert_evaluators_match_catalogue()

    Trophy = apps.get_model("trophies", "Trophy")
    for entry in TROPHY_CATALOGUE:
        code, name, description, category, rarity, icon, sort_order = entry
        Trophy.objects.update_or_create(
            code=code,
            defaults={
                "name":        name,
                "description": description,
                "category":    category,
                "rarity":      rarity,
                "icon":        icon,
                "sort_order":  sort_order,
            },
        )


def unseed_trophies(apps, schema_editor):
    """Reverse: remove all catalogue rows. Awards cascade-delete via
    the FK so we don't have to clean those up manually."""
    Trophy = apps.get_model("trophies", "Trophy")
    Trophy.objects.all().delete()


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Trophy",
            fields=[
                ("id",          models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("code",        models.CharField(max_length=80, unique=True)),
                ("name",        models.CharField(max_length=100)),
                ("description", models.CharField(max_length=240)),
                ("category",    models.CharField(max_length=40, choices=[
                    ("workout_volume",   "Workout Volume"),
                    ("streaks",          "Streaks"),
                    ("frequency",        "Frequency"),
                    ("personal_record",  "Personal Records"),
                    ("reps_sets",        "Reps & Sets"),
                    ("time_special",     "Time & Special Days"),
                    ("check_ins",        "Check-ins & Progress"),
                    ("nutrition",        "Nutrition & Hydration"),
                    ("body",             "Body Composition"),
                ])),
                ("rarity",      models.CharField(max_length=20, choices=[
                    ("common",    "Common"),
                    ("uncommon",  "Uncommon"),
                    ("rare",      "Rare"),
                    ("epic",      "Epic"),
                    ("legendary", "Legendary"),
                ])),
                ("icon",        models.CharField(max_length=80)),
                ("sort_order",  models.IntegerField(default=0)),
            ],
            options={
                "ordering": ["category", "sort_order", "id"],
            },
        ),
        migrations.CreateModel(
            name="ClientTrophyAward",
            fields=[
                ("id",        models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("earned_at", models.DateTimeField(auto_now_add=True)),
                ("trophy",    models.ForeignKey(on_delete=models.deletion.CASCADE, to="trophies.trophy")),
                ("user",      models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="trophy_awards", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(fields=["user", "trophy"], name="unique_user_trophy"),
                ],
                "indexes": [
                    models.Index(fields=["user", "-earned_at"], name="trophies_cl_user_id_e58f88_idx"),
                ],
            },
        ),
        migrations.RunPython(seed_trophies, reverse_code=unseed_trophies),
    ]
