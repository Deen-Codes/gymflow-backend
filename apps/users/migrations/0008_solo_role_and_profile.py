# E.1 / SOLO-01 — adds the SOLO role to User.role choices and creates
# the SoloProfile model that holds onboarding answers + subscription
# state.
#
# Field-only changes are reversible; the new model is straightforward
# to drop in a rollback.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0007_sso_avatar_prefs_city"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(
                choices=[
                    ("trainer", "Trainer"),
                    ("client",  "Client"),
                    ("solo",    "Solo"),
                ],
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="SoloProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("goals",      models.JSONField(blank=True, default=list)),
                ("experience", models.CharField(
                    blank=True, default="", max_length=20,
                    choices=[
                        ("just_starting",    "Just starting"),
                        ("under_one_year",   "0–1 year"),
                        ("one_to_three",     "1–3 years"),
                        ("three_plus",       "3+ years"),
                    ],
                )),
                ("equipment",  models.CharField(
                    blank=True, default="", max_length=20,
                    choices=[
                        ("full_gym",          "Full gym"),
                        ("home_with_weights", "Home with weights"),
                        ("bodyweight_only",   "Bodyweight only"),
                        ("mixed",             "Mixed"),
                    ],
                )),
                ("days_per_week", models.PositiveSmallIntegerField(default=3)),
                ("tier", models.CharField(
                    default="free", max_length=10,
                    choices=[
                        ("free",   "Free"),
                        ("pro",    "Pro"),
                        ("pro_ai", "Pro AI"),
                    ],
                )),
                ("tier_active_until", models.DateTimeField(blank=True, null=True)),
                ("trial_started_at",  models.DateTimeField(blank=True, null=True)),
                ("trial_ends_at",     models.DateTimeField(blank=True, null=True)),
                ("stripe_subscription_id", models.CharField(blank=True, default="", max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="solo_profile",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
        ),
    ]
