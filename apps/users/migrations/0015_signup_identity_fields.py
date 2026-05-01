# SIGNUP-RESTRUCTURE (D-AFK.4) — identity fields captured at signup
# instead of nudged out via tabs / chat. Gender uses a single
# inclusive list; sex_at_birth is optional and ONLY consulted by
# macro calc (BMR formulas are sex-keyed at the biology level).
# height_cm complements bodyweight_kg for kcal computation.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0014_cardio_mutations"),
    ]

    operations = [
        migrations.AddField(
            model_name="soloprofile",
            name="gender",
            field=models.CharField(
                max_length=16,
                choices=[
                    ("male",       "Male"),
                    ("female",     "Female"),
                    ("non_binary", "Non-binary"),
                    ("prefer_not", "Prefer not to say"),
                ],
                blank=True,
                default="",
            ),
        ),
        migrations.AddField(
            model_name="soloprofile",
            name="sex_at_birth",
            field=models.CharField(
                max_length=8,
                choices=[
                    ("male",   "Male"),
                    ("female", "Female"),
                    ("",       "Unspecified"),
                ],
                blank=True,
                default="",
            ),
        ),
        migrations.AddField(
            model_name="soloprofile",
            name="height_cm",
            field=models.PositiveSmallIntegerField(null=True, blank=True),
        ),
    ]
