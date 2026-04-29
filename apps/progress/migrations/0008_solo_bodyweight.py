# D.2.1 — body-weight history for Solo users.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("progress", "0007_system_field_questions"),
    ]

    operations = [
        migrations.CreateModel(
            name="SoloBodyweightLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("logged_on", models.DateField(db_index=True, default=django.utils.timezone.localdate)),
                ("kg", models.FloatField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="solo_bodyweight_logs",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"ordering": ["-logged_on"]},
        ),
        migrations.AddConstraint(
            model_name="solobodyweightlog",
            constraint=models.UniqueConstraint(
                fields=("user", "logged_on"),
                name="unique_solo_bodyweight_per_day",
            ),
        ),
    ]
