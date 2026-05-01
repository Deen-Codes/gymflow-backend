# EXERCISE-FOUNDATION — link Exercise rows to ExerciseCatalog +
# add animation_url to the catalog so the iOS workout view can
# surface form-demo animations on the exercise card.
#
# Three operations:
#   1. ExerciseCatalog.animation_url (nullable URL field).
#   2. Exercise.catalog_item FK → ExerciseCatalog (nullable, SET_NULL).
#   3. Data migration: backfill catalog_item on every existing
#      Exercise row by case-insensitive name match against the
#      ExerciseCatalog. Rows with no match stay null (custom /
#      AI-generated exercises that aren't in the catalog yet).
#
# The animation_url field stays empty for now — assets land via
# the commissioned library project, not this migration. The
# iOS ExerciseAnimationView gracefully falls back to image_url
# (already populated for the wger imports) then to an SF symbol
# when neither's set.

from django.db import migrations, models
import django.db.models.deletion


def backfill_catalog_links(apps, schema_editor):
    """For every Exercise row, try to match its `name` against an
    ExerciseCatalog row case-insensitively and link them. Imperfect
    by design — the canonical 1:1 mapping happens at write time
    going forward; this just back-populates legacy rows so
    existing users see image_url + (future) animation_url
    immediately."""
    Exercise = apps.get_model("workouts", "Exercise")
    ExerciseCatalog = apps.get_model("workouts", "ExerciseCatalog")

    # Build a name → catalog_id lookup once. Case-insensitive,
    # trimmed. If two catalog rows share a name (shouldn't happen
    # often), the first one wins.
    lookup = {}
    for cat in ExerciseCatalog.objects.filter(is_published=True):
        key = (cat.name or "").strip().lower()
        if key and key not in lookup:
            lookup[key] = cat.id

    matched = 0
    unmatched = 0
    for ex in Exercise.objects.filter(catalog_item__isnull=True).iterator():
        key = (ex.name or "").strip().lower()
        cat_id = lookup.get(key)
        if cat_id:
            ex.catalog_item_id = cat_id
            ex.save(update_fields=["catalog_item"])
            matched += 1
        else:
            unmatched += 1

    # Print a summary so the deploy log shows backfill effectiveness.
    print(
        f"[exercise-foundation] backfill: matched={matched} "
        f"unmatched={unmatched} (unmatched rows stay null — they're "
        f"AI-generated / custom names not in the catalog)"
    )


def noop_reverse(apps, schema_editor):
    """Reverse just clears the FK; the schema-level reverse drops
    the column anyway."""


class Migration(migrations.Migration):

    dependencies = [
        ("workouts", "0006_solo_template"),
    ]

    operations = [
        migrations.AddField(
            model_name="exercisecatalog",
            name="animation_url",
            field=models.URLField(blank=True),
        ),
        migrations.AddField(
            model_name="exercise",
            name="catalog_item",
            field=models.ForeignKey(
                to="workouts.exercisecatalog",
                on_delete=django.db.models.deletion.SET_NULL,
                null=True, blank=True,
                related_name="workout_exercises",
            ),
        ),
        migrations.RunPython(backfill_catalog_links, noop_reverse),
    ]
