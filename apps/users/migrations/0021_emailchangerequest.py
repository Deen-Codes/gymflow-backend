"""EMAIL-EDIT — EmailChangeRequest table for the 6-digit OTP flow."""
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0020_solo_bodystat_timestamps"),
    ]

    operations = [
        migrations.CreateModel(
            name="EmailChangeRequest",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("new_email", models.EmailField(max_length=254)),
                ("code", models.CharField(db_index=True, max_length=12)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField()),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                ("requested_ip", models.GenericIPAddressField(blank=True, null=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="email_change_requests",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Email change request",
                "verbose_name_plural": "Email change requests",
                "ordering": ["-created_at"],
            },
        ),
    ]
