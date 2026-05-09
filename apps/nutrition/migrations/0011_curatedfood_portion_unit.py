# FOOD-DB-V2-SCHEMA — portion-unit support on CuratedFood.
#
# Adds two columns:
#   • portion_unit  — choice field (grams, ml, piece, slice, wrap,
#                     scoop, tbsp, tsp, cup, oz, egg, bar, can,
#                     bottle, pack, pint, shot). Defaults to "grams"
#                     for backward compat — every existing seeded
#                     row stays valid.
#   • unit_grams    — float, nullable. Gram-equivalent of 1 unit.
#                     Only required when portion_unit != "grams".
#
# Why: "1 egg" / "1 slice of bread" / "1 wrap" need to be first-class
# portions in the picker. Macros stay stored per-100g; the picker
# multiplies by `unit_grams * N` for unit-based foods.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nutrition", "0010_curatedfood_tagging"),
    ]

    operations = [
        migrations.AddField(
            model_name="curatedfood",
            name="portion_unit",
            field=models.CharField(
                blank=False,
                db_index=True,
                default="grams",
                max_length=10,
                choices=[
                    ("grams",  "Grams"),
                    ("ml",     "Millilitres"),
                    ("piece",  "Piece"),
                    ("slice",  "Slice"),
                    ("wrap",   "Wrap"),
                    ("scoop",  "Scoop"),
                    ("tbsp",   "Tablespoon"),
                    ("tsp",    "Teaspoon"),
                    ("cup",    "Cup"),
                    ("oz",     "Ounce"),
                    ("egg",    "Egg"),
                    ("bar",    "Bar"),
                    ("can",    "Can"),
                    ("bottle", "Bottle"),
                    ("pack",   "Pack"),
                    ("pint",   "Pint"),
                    ("shot",   "Shot"),
                    ("meal",   "Meal"),
                ],
            ),
        ),
        migrations.AddField(
            model_name="curatedfood",
            name="unit_grams",
            field=models.FloatField(blank=True, null=True),
        ),
    ]
