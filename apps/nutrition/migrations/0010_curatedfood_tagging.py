# FOOD-DB-TAGGING — structured dietary_compat + allergens columns
# on CuratedFood. Both nullable/blank so existing rows (none yet
# in production) stay valid; the import command auto-tags rows
# on ingest.
#
# Indexed because the AI nutrition builder filters by these
# columns at request time ("give me a meal plan that's halal,
# tree-nut-free") and we want the filters to be cheap.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nutrition", "0009_curated_food"),
    ]

    operations = [
        migrations.AddField(
            model_name="curatedfood",
            name="dietary_compat",
            field=models.CharField(blank=True, db_index=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="curatedfood",
            name="allergens",
            field=models.CharField(blank=True, db_index=True, default="", max_length=128),
        ),
    ]
