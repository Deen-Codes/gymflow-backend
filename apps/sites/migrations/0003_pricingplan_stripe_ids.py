"""Add lazy Stripe Product + Price ID cache to PricingPlan.

Populated the first time a client subscribes to that tier. Empty
string = not yet synced to the trainer's connected account.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sites", "0002_pricingplan_and_pricing_section"),
    ]

    operations = [
        migrations.AddField(
            model_name="pricingplan",
            name="stripe_product_id",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="pricingplan",
            name="stripe_price_id",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
    ]
