# D.2.2 — Progress photos.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("progress", "0008_solo_bodyweight"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProgressPhoto",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("category", models.CharField(
                    choices=[("front","Front"),("side","Side"),("back","Back"),("other","Other")],
                    default="front", max_length=8,
                )),
                ("image_base64", models.TextField()),
                ("bodyweight_kg", models.FloatField(blank=True, null=True)),
                ("note", models.CharField(blank=True, default="", max_length=255)),
                ("taken_on", models.DateField(db_index=True, default=django.utils.timezone.localdate)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="progress_photos",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"ordering": ["-taken_on", "-created_at"]},
        ),
    ]
