# T1.7 — NutritionTemplate model. Free-tier curated nutrition plans
# scaled deterministically by bodyweight + goal (no AI cost).
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nutrition", "0011_curatedfood_portion_unit"),
    ]

    operations = [
        migrations.CreateModel(
            name="NutritionTemplate",
            fields=[
                ("id", models.AutoField(
                    auto_created=True, primary_key=True, serialize=False,
                    verbose_name="ID",
                )),
                ("slug", models.SlugField(max_length=64, unique=True)),
                ("name", models.CharField(max_length=80)),
                ("tagline", models.CharField(blank=True, max_length=160)),
                ("summary", models.TextField(blank=True)),
                ("protein_g_per_kg",   models.FloatField(default=1.8)),
                ("fat_g_per_kg",       models.FloatField(default=0.8)),
                ("kcal_delta_vs_tdee", models.IntegerField(default=0)),
                ("goal_alignment",        models.CharField(blank=True, default="", max_length=128)),
                ("dietary_compatibility", models.CharField(blank=True, default="", max_length=128)),
                ("pace_label",  models.CharField(blank=True, default="", max_length=80)),
                ("sort_order",  models.PositiveSmallIntegerField(default=100)),
                ("is_published", models.BooleanField(default=True)),
                ("created_at",  models.DateTimeField(auto_now_add=True)),
                ("updated_at",  models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["sort_order", "name"],
            },
        ),
    ]
