"""Phase 35 — index hot-path fields on ClientSubscription.

Why each index matters:
  • stripe_customer_id    — looked up on every Customer Portal email
                            request via SpotifySessionStore-equivalent
                            ClientSubscription.objects.filter(...)
  • stripe_subscription_id — every Stripe webhook event arrives keyed
                             by this id; without an index every event
                             is a sequential scan.
  • (trainer, status)     — composite index for "all my active subs"
                             on the dashboard subscription panel.
  • (client, -created_at) — fast lookup of "this client's most recent
                            subscription" used by the iOS portal endpoint.

These are pure additive index builds — safe to run on a populated table.
Postgres builds them concurrently by default in modern versions.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="clientsubscription",
            name="stripe_customer_id",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
        migrations.AlterField(
            model_name="clientsubscription",
            name="stripe_subscription_id",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
        migrations.AddIndex(
            model_name="clientsubscription",
            index=models.Index(
                fields=["trainer", "status"],
                name="payments_cl_trainer_status_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="clientsubscription",
            index=models.Index(
                fields=["client", "-created_at"],
                name="payments_cl_client_recent_idx",
            ),
        ),
    ]
