# Generated for NUTRITION-ONBOARDING-FIX (#219).
#
# Drops SoloProfile.target_calories / target_protein / target_carbs /
# target_fats defaults from 2200 / 140 / 240 / 70 to 0. Combined with
# the removal of the auto-compute call in `_ensure_solo` and the
# solo signup view, this means a fresh signup lands on the Nutrition
# tab with `target_calories == 0`, which is the gate iOS uses to show
# the cinematic onboarding empty-state. Without this change the
# onboarding never surfaced because every fresh profile already had
# bogus default macros.
#
# Existing rows are intentionally NOT backfilled to 0 — users who
# already chose a plan keep their targets. Only newly-created profiles
# benefit from the fixed default. Anyone wanting to re-trigger the
# onboarding can use the Profile debug "factory reset" surface, which
# already wipes SoloProfile.target_*.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0015_signup_identity_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="soloprofile",
            name="target_calories",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name="soloprofile",
            name="target_protein",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name="soloprofile",
            name="target_carbs",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name="soloprofile",
            name="target_fats",
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
