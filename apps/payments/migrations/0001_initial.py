from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("users", "0004_trainerprofile_stripe_user_id"),
        ("sites", "0002_pricingplan_and_pricing_section"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="StripeOAuthState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("state", models.CharField(max_length=64, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("trainer", models.ForeignKey(
                    on_delete=models.deletion.CASCADE,
                    related_name="stripe_oauth_states",
                    to="users.trainerprofile",
                )),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="ClientSubscription",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("stripe_customer_id",     models.CharField(max_length=64, blank=True, default="")),
                ("stripe_subscription_id", models.CharField(max_length=64, blank=True, default="")),
                ("status", models.CharField(
                    max_length=20, default="incomplete",
                    choices=[
                        ("active",     "Active"),
                        ("trialing",   "Trialing"),
                        ("past_due",   "Past due"),
                        ("canceled",   "Canceled"),
                        ("incomplete", "Incomplete"),
                    ],
                )),
                ("current_period_end",   models.DateTimeField(blank=True, null=True)),
                ("cancel_at_period_end", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("trainer", models.ForeignKey(
                    on_delete=models.deletion.CASCADE,
                    related_name="client_subscriptions",
                    to="users.trainerprofile",
                )),
                ("client", models.ForeignKey(
                    on_delete=models.deletion.CASCADE,
                    related_name="trainer_subscriptions",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("plan", models.ForeignKey(
                    on_delete=models.deletion.SET_NULL,
                    null=True, blank=True,
                    related_name="subscriptions",
                    to="sites.pricingplan",
                )),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
