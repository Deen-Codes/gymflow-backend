# N.1.1 — daily macro targets stored on SoloProfile + bodyweight
# (which the user enters via Apple Health sync or first check-in;
# defaults to 75kg until set).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0009_solo_assigned_plan"),
    ]

    operations = [
        migrations.AddField(
            model_name="soloprofile",
            name="target_calories",
            field=models.PositiveIntegerField(default=2200),
        ),
        migrations.AddField(
            model_name="soloprofile",
            name="target_protein",
            field=models.PositiveSmallIntegerField(default=140),
        ),
        migrations.AddField(
            model_name="soloprofile",
            name="target_carbs",
            field=models.PositiveSmallIntegerField(default=240),
        ),
        migrations.AddField(
            model_name="soloprofile",
            name="target_fats",
            field=models.PositiveSmallIntegerField(default=70),
        ),
        migrations.AddField(
            model_name="soloprofile",
            name="bodyweight_kg",
            field=models.FloatField(blank=True, null=True),
        ),
    ]
