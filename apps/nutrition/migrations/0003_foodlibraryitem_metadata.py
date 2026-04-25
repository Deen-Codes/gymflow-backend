from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nutrition", "0002_foodlibraryitem_nutritionmeal_nutritionmealitem"),
    ]

    operations = [
        migrations.AddField(
            model_name="foodlibraryitem",
            name="source",
            field=models.CharField(
                choices=[("custom", "Custom"), ("off", "Open Food Facts")],
                default="custom",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="foodlibraryitem",
            name="external_id",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="foodlibraryitem",
            name="brand",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddConstraint(
            model_name="foodlibraryitem",
            constraint=models.UniqueConstraint(
                condition=models.Q(("external_id", ""), _negated=True),
                fields=("user", "source", "external_id"),
                name="unique_food_library_external_per_trainer",
            ),
        ),
    ]
