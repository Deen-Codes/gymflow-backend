from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("users", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="TrainerSite",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("is_published", models.BooleanField(default=False)),
                ("brand_color", models.CharField(blank=True, default="", max_length=7)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("trainer", models.OneToOneField(
                    on_delete=models.deletion.CASCADE,
                    related_name="site",
                    to="users.trainerprofile",
                )),
            ],
        ),
        migrations.CreateModel(
            name="SiteSection",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("section_type", models.CharField(
                    choices=[
                        ("hero",         "Hero"),
                        ("about",        "About"),
                        ("services",     "Services"),
                        ("testimonials", "Testimonials"),
                        ("onboarding",   "Onboarding form"),
                        ("footer",       "Footer"),
                    ],
                    max_length=32,
                )),
                ("order", models.IntegerField(default=0)),
                ("is_visible", models.BooleanField(default=True)),
                ("is_required", models.BooleanField(default=False)),
                ("content", models.JSONField(blank=True, default=dict)),
                ("site", models.ForeignKey(
                    on_delete=models.deletion.CASCADE,
                    related_name="sections",
                    to="sites.trainersite",
                )),
            ],
            options={"ordering": ["order", "id"]},
        ),
        migrations.CreateModel(
            name="PublicSignup",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("full_name", models.CharField(max_length=255)),
                ("email", models.EmailField(max_length=254)),
                ("raw_answers", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("client_user", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=models.deletion.SET_NULL,
                    related_name="public_signup",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("site", models.ForeignKey(
                    on_delete=models.deletion.CASCADE,
                    related_name="signups",
                    to="sites.trainersite",
                )),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
