"""
Lazy sync of GymFlow's PricingPlan rows → Stripe Products + Prices on
the trainer's connected account.

Why lazy: we only need the Stripe-side resources to exist when the
*first* customer is about to subscribe. Doing it on tier-creation
would mean re-syncing every time a trainer edits a row, and would
break if the trainer hasn't connected Stripe yet.

Public API:
    get_or_create_price_for_plan(plan) -> (price_id, error)
        Creates a Product + Price on the trainer's connected account
        if needed, caches the IDs on the plan, returns the price ID.
        On any Stripe error returns (None, message).
"""
from typing import Tuple, Optional

from .stripe_client import get_stripe


# Map our intervals → Stripe's recurring shape. Oneshot is a one-time
# Price (no `recurring` block on the Stripe Price).
_INTERVAL_TO_STRIPE = {
    "weekly":  {"interval": "week",  "interval_count": 1},
    "monthly": {"interval": "month", "interval_count": 1},
    "yearly":  {"interval": "year",  "interval_count": 1},
}


def get_or_create_price_for_plan(plan) -> Tuple[Optional[str], Optional[str]]:
    """Return (price_id, None) on success, (None, error_message) on failure."""

    if plan.stripe_price_id:
        return plan.stripe_price_id, None

    trainer = plan.trainer
    if not trainer.stripe_user_id:
        return None, "Trainer has not connected Stripe yet."

    stripe = get_stripe()
    acct = trainer.stripe_user_id

    try:
        # 1. Product (created once per plan, reused for all Prices)
        product_id = plan.stripe_product_id
        if not product_id:
            product = stripe.Product.create(
                name=plan.name,
                description=plan.description or None,
                metadata={
                    "gymflow_plan_id":    plan.id,
                    "gymflow_trainer_id": trainer.id,
                },
                stripe_account=acct,
            )
            product_id = product.id
            plan.stripe_product_id = product_id

        # 2. Price (immutable on Stripe — recreated if the plan's
        #    pennies/interval/currency change. We don't detect drift
        #    yet; that's a follow-up.)
        price_kwargs = {
            "product":     product_id,
            "unit_amount": plan.price_pennies,
            "currency":    plan.currency.lower(),
            "stripe_account": acct,
            "metadata": {"gymflow_plan_id": plan.id},
        }
        recurring = _INTERVAL_TO_STRIPE.get(plan.interval)
        if recurring is not None:
            price_kwargs["recurring"] = recurring

        price = stripe.Price.create(**price_kwargs)
        plan.stripe_price_id = price.id

        # Persist both IDs at once
        plan.save(update_fields=["stripe_product_id", "stripe_price_id"])
        return price.id, None

    except Exception as exc:
        return None, f"Stripe error creating Price: {exc}"
