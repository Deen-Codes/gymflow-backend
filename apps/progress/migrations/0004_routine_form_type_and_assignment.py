from django.conf import settings
from django.db import migrations, models


def rewrite_weekly_to_routine(apps, schema_editor):
    """Existing rows with form_type='weekly' carry the same questions
    as the new 'routine' type — just rename the slug."""
    CheckInForm = apps.get_model("progress", "CheckInForm")
    CheckInForm.objects.filter(form_type="weekly").update(form_type="routine")


def reverse_routine_to_weekly(apps, schema_editor):
    CheckInForm = apps.get_model("progress", "CheckInForm")
    CheckInForm.objects.filter(form_type="routine").update(form_type="weekly")


class Migration(migrations.Migration):

    dependencies = [
        ("progress", "0003_checkinsubmission_checkinanswer"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # 1. Update CheckInForm.form_type choices (no schema change —
        #    Django CharField choices live in Python only). The
        #    AlterField makes the migration record consistent with the
        #    model so future makemigrations doesn't churn.
        migrations.AlterField(
            model_name="checkinform",
            name="form_type",
            field=models.CharField(
                choices=[
                    ("onboarding", "Onboarding"),
                    ("daily", "Daily check-in"),
                    ("routine", "Routine check-in"),
                ],
                max_length=20,
            ),
        ),

        # 2. Migrate any existing 'weekly' rows over to 'routine'.
        migrations.RunPython(rewrite_weekly_to_routine, reverse_routine_to_weekly),

        # 3. Add the new ClientCheckInAssignment table.
        migrations.CreateModel(
            name="ClientCheckInAssignment",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("cadence", models.CharField(
                    choices=[
                        ("oneshot",  "One-shot"),
                        ("daily",    "Every day"),
                        ("weekly",   "Every week"),
                        ("biweekly", "Every 2 weeks"),
                        ("monthly",  "Every month"),
                    ],
                    max_length=20,
                )),
                ("is_active", models.BooleanField(default=True)),
                ("last_submitted_at", models.DateTimeField(blank=True, null=True)),
                ("next_due_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("client", models.ForeignKey(
                    on_delete=models.deletion.CASCADE,
                    related_name="checkin_assignments",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("form", models.ForeignKey(
                    on_delete=models.deletion.CASCADE,
                    related_name="client_assignments",
                    to="progress.checkinform",
                )),
            ],
            options={
                "ordering": ["client__username", "form__form_type"],
            },
        ),
        migrations.AddConstraint(
            model_name="clientcheckinassignment",
            constraint=models.UniqueConstraint(
                fields=("client", "form"),
                name="unique_assignment_per_client_per_form",
            ),
        ),
    ]
