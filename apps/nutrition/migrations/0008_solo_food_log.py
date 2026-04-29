# N.1.1 — Solo food log model. Solo users track macros against
# default targets (no trainer-built meal plan).

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("nutrition", "0007_portion_unit_expansion"),
    ]

    operations = [
        migrations.CreateModel(
            name="SoloFoodLogEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255)),
                ("portion", models.FloatField(default=100)),
                ("calories", models.FloatField(default=0)),
                ("protein", models.FloatField(default=0)),
                ("carbs", models.FloatField(default=0)),
                ("fats", models.FloatField(default=0)),
                ("consumed_on", models.DateField(db_index=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("food", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="solo_log_entries",
                    to="nutrition.foodlibraryitem",
                )),
                ("user", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="solo_food_log",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"ordering": ["-consumed_on", "-created_at"]},
        ),
        migrations.AddIndex(
            model_name="solofoodlogentry",
            index=models.Index(fields=["user", "consumed_on"], name="nutrition_s_user_id_d2c4ce_idx"),
        ),
    ]
