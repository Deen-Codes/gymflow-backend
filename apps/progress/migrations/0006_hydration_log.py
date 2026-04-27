"""HydrationLog model — backs the iOS HomeWaterCard with server-of-
record cup counts so the water trophies (8 Cups in a Day, 7-Day
Hydration, 100 Days Hydrated) can be evaluated server-side and
multi-device users see consistent state."""
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):
    dependencies = [
        ("progress", "0005_field_key_index"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="HydrationLog",
            fields=[
                ("id",         models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("logged_on",  models.DateField(default=timezone.localdate)),
                ("cups",       models.PositiveSmallIntegerField(default=0)),
                ("goal_cups",  models.PositiveSmallIntegerField(default=8)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("client",     models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="hydration_logs", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-logged_on"],
                "constraints": [
                    models.UniqueConstraint(fields=["client", "logged_on"], name="unique_hydration_per_client_per_day"),
                ],
                "indexes": [
                    models.Index(fields=["client", "-logged_on"], name="progress_hy_client__c11df0_idx"),
                ],
            },
        ),
    ]
