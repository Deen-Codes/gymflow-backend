"""
Phase 7.7.1 — Stripe Connect models.

* StripeOAuthState — short-lived CSRF nonce we send out with the
  authorize URL and verify on callback so an attacker can't trick a
  trainer into granting access to a different Stripe account.

* ClientSubscription — one row per active subscription a client has
  with their trainer. Created by the webhook handler (next batch);
  the model + its admin live here now so the rest of the schema is
  ready when the money flow lands.
"""
from django.conf import settings
from django.db import models

from apps.users.models import TrainerProfile
from apps.sites.models import PricingPlan


class StripeOAuthState(models.Model):
    """One-shot nonce used during the Stripe Connect OAuth dance."""
    state = models.CharField(max_length=64, unique=True)
    trainer = models.ForeignKey(
        TrainerProfile, on_delete=models.CASCADE,
        related_name="stripe_oauth_states",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class ClientSubscription(models.Model):
    """A client's active subscription to one of their trainer's tiers."""

    STATUS_ACTIVE   = "active"
    STATUS_TRIALING = "trialing"
    STATUS_PAST_DUE = "past_due"
    STATUS_CANCELED = "canceled"
    STATUS_INCOMPLETE = "incomplete"

    STATUS_CHOICES = [
        (STATUS_ACTIVE,     "Active"),
        (STATUS_TRIALING,   "Trialing"),
        (STATUS_PAST_DUE,   "Past due"),
        (STATUS_CANCELED,   "Canceled"),
        (STATUS_INCOMPLETE, "Incomplete"),
    ]

    trainer = models.ForeignKey(
        TrainerProfile,
        on_delete=models.CASCADE,
        related_name="client_subscriptions",
    )
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="trainer_subscriptions",
    )
    plan = models.ForeignKey(
        PricingPlan,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="subscriptions",
    )

    # Mirrors Stripe — kept here so the trainer dashboard can render
    # state without re-hitting the API on every request.
    stripe_customer_id     = models.CharField(max_length=64, blank=True, default="")
    stripe_subscription_id = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_INCOMPLETE)
    current_period_end = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.client.username} → {self.trainer} ({self.status})"
