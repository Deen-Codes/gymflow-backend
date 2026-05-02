# NUTRITION-DB (#105) — owned multi-region food catalog. Read-only
# at runtime; populated via the `import_curated_foods` management
# command from USDA / UK FSA / AUSNUT / CIQUAL / Marrow-curated
# sources. See the model docstring for the full provenance table
# and licensing rationale (Open Food Facts is intentionally
# excluded — CC BY-SA blocks commercial bake-in).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nutrition", "0008_solo_food_log"),
    ]

    operations = [
        migrations.CreateModel(
            name="CuratedFood",
            fields=[
                ("id", models.AutoField(
                    auto_created=True, primary_key=True,
                    serialize=False, verbose_name="ID",
                )),
                ("source", models.CharField(choices=[
                    ("usda",   "USDA FoodData Central"),
                    ("fsa_uk", "UK FSA McCance & Widdowson's"),
                    ("ausnut", "AUSNUT 2011-13"),
                    ("ciqual", "CIQUAL"),
                    ("marrow", "Marrow curated"),
                ], db_index=True, max_length=12)),
                ("source_id", models.CharField(db_index=True, max_length=64)),
                ("name",      models.CharField(db_index=True, max_length=200)),
                ("brand",     models.CharField(blank=True, default="", max_length=120)),
                ("barcode",   models.CharField(blank=True, default="", db_index=True, max_length=32)),
                ("region_codes",  models.CharField(blank=True, default="", max_length=64)),
                ("kcal_per_100g",     models.FloatField()),
                ("protein_per_100g",  models.FloatField()),
                ("carbs_per_100g",    models.FloatField()),
                ("fat_per_100g",      models.FloatField()),
                ("serving_grams",     models.FloatField(blank=True, null=True)),
                ("serving_label",     models.CharField(blank=True, default="", max_length=40)),
                ("tags",       models.CharField(blank=True, default="", max_length=200)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "indexes": [
                    models.Index(fields=["name"], name="nutrition_c_name_idx"),
                    models.Index(fields=["barcode"], name="nutrition_c_barcode_idx"),
                ],
                "unique_together": {("source", "source_id")},
            },
        ),
    ]
