from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sites", "0001_initial"),
        ("users", "0001_initial"),
    ]

    operations = [
        # 1. Update SiteSection.section_type choices to include "pricing"
        migrations.AlterField(
            model_name="sitesection",
            name="section_type",
            field=models.CharField(
                choices=[
                    ("hero",         "Hero"),
                    ("about",        "About"),
                    ("services",     "Services"),
                    ("pricing",      "Pricing"),
                    ("testimonials", "Testimonials"),
                    ("onboarding",   "Onboarding form"),
                    ("footer",       "Footer"),
                ],
                max_length=32,
            ),
        ),

        # 2. New PricingPlan table
        migrations.CreateModel(
            name="PricingPlan",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("description", models.TextField(blank=True, default="")),
                ("price_pennies", models.IntegerField(default=0)),
                ("currency", models.CharField(default="GBP", max_length=3)),
                ("interval", models.CharField(
                    choices=[
                        ("monthly", "per month"),
                        ("weekly",  "per week"),
                        ("yearly",  "per year"),
                        ("oneshot", "one-time"),
                    ],
                    default="monthly",
                    max_length=20,
                )),
                ("sort_order", models.IntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                ("is_featured", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("trainer", models.ForeignKey(
                    on_delete=models.deletion.CASCADE,
                    related_name="pricing_plans",
                    to="users.trainerprofile",
                )),
            ],
            options={"ordering": ["sort_order", "id"]},
        ),
    ]
