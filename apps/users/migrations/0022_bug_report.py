"""Adds BugReport model — user-submitted bug reports.

REPORT-A-BUG (May 2026, Deen QC) — see DECISIONS / TASKS_OPEN for the
shipping context. Each row is a single submission from the iOS Profile
sheet. Resend fires the email notification on create; the row is the
canonical record + drives Django admin triage.

Schema mirrors `BugReport` in `apps/users/models.py`. Screenshot bytes
land as a base64 TextField (same pattern as User.avatar_base64 and
ProgressPhoto.image_base64) — no external storage infra needed.
"""
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0021_emailchangerequest"),
    ]

    operations = [
        migrations.CreateModel(
            name="BugReport",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("what_happened", models.TextField()),
                ("expected", models.TextField(blank=True, default="")),
                ("app_version", models.CharField(blank=True, default="", max_length=32)),
                ("app_build", models.CharField(blank=True, default="", max_length=32)),
                ("os_version", models.CharField(blank=True, default="", max_length=32)),
                ("device_model", models.CharField(blank=True, default="", max_length=64)),
                ("recent_actions", models.JSONField(blank=True, default=list)),
                ("screenshot_base64", models.TextField(blank=True, default="")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("open", "Open"),
                            ("resolved", "Resolved"),
                            ("wontfix", "Won't fix"),
                            ("dupe", "Duplicate"),
                        ],
                        db_index=True,
                        default="open",
                        max_length=12,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="bug_reports",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
