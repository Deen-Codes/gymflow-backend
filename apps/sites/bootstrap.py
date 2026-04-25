"""Lazy bootstrap for a trainer's site — runs on first visit to the
editor and ensures TrainerSite + the 6 default sections exist with
sensible default content for each widget type."""
from django.utils.text import slugify

from .models import TrainerSite, SiteSection, PricingPlan


def _default_content(section_type, trainer_profile):
    """Type-specific seed content. Avoids the trainer staring at empty
    fields on first load; they edit-in-place from there."""
    name = (
        trainer_profile.business_name
        or trainer_profile.user.get_full_name()
        or trainer_profile.user.username
    )
    if section_type == SiteSection.HERO:
        return {
            "headline": f"Train with {name}",
            "subheadline": "1-on-1 online coaching for serious results. Personalised plans, weekly check-ins, real progress.",
            "image_url": "",
            "primary_cta": "Apply now",
            "secondary_cta": "What you get",
        }
    if section_type == SiteSection.ABOUT:
        return {
            "heading": f"About {name}",
            "body": (
                "I help motivated clients build the body and habits they want — without "
                "burning out. Two decades of coaching condensed into a programme that "
                "respects your life outside the gym."
            ),
            "photo_url": "",
        }
    if section_type == SiteSection.SERVICES:
        return {
            "heading": "What you get",
            "items": [
                {"title": "Custom training plan", "description": "Built around your schedule, equipment, and goals. Updated weekly."},
                {"title": "Nutrition coaching",   "description": "Macros that fit how you actually eat. No off-the-shelf meal plans."},
                {"title": "Weekly check-ins",     "description": "Photos, weight, mood — I read every one and adjust your plan."},
            ],
        }
    if section_type == SiteSection.PRICING:
        return {
            "heading": "Coaching plans",
            "lede": "Pick what fits — every tier includes weekly check-ins and 24/7 messaging.",
            # The actual tier cards are pulled from the trainer's
            # PricingPlan rows at render time. This blob just stores
            # framing copy so trainer edits don't blow away tiers.
        }
    if section_type == SiteSection.TESTIMONIALS:
        return {
            "heading": "Recent wins",
            "items": [
                {"quote": "Best decision I made this year. Lost 9kg in 16 weeks without giving up my social life.", "author": "Sam, 34"},
                {"quote": "I've trained for 10 years — this is the first time someone actually adapted plans to my schedule.", "author": "Priya, 29"},
            ],
        }
    if section_type == SiteSection.ONBOARDING:
        return {
            "heading": "Apply for coaching",
            "lede": "Tell me about you and your goals. I read every application personally.",
            "submit_label": "Send application",
        }
    if section_type == SiteSection.FOOTER:
        return {
            "tagline": f"{name} · Coaching that actually works.",
            "instagram": "",
            "twitter": "",
            "email": trainer_profile.user.email,
        }
    return {}


def ensure_site(trainer_profile):
    """Get-or-create a TrainerSite + its 6 default sections.
    Idempotent — safe to call on every editor visit."""
    site, _ = TrainerSite.objects.get_or_create(trainer=trainer_profile)
    existing_types = set(site.sections.values_list("section_type", flat=True))

    for index, section_type in enumerate(SiteSection.DEFAULT_ORDER):
        if section_type in existing_types:
            continue
        SiteSection.objects.create(
            site=site,
            section_type=section_type,
            order=index,
            is_visible=True,
            is_required=section_type in SiteSection.REQUIRED_TYPES,
            content=_default_content(section_type, trainer_profile),
        )

    # Seed a couple of starter pricing tiers if the trainer has none.
    # Pricing rows live on PricingPlan, not on the SiteSection content
    # blob, so they survive section deletes and re-orders.
    if not PricingPlan.objects.filter(trainer=trainer_profile).exists():
        PricingPlan.objects.create(
            trainer=trainer_profile,
            name="Standard",
            description="Custom training + nutrition plan, weekly check-ins, in-app messaging.",
            price_pennies=15000, currency="GBP",
            interval=PricingPlan.INTERVAL_MONTHLY,
            sort_order=1, is_active=True, is_featured=False,
        )
        PricingPlan.objects.create(
            trainer=trainer_profile,
            name="Premium",
            description="Everything in Standard plus form-check video reviews and a monthly 1:1 call.",
            price_pennies=25000, currency="GBP",
            interval=PricingPlan.INTERVAL_MONTHLY,
            sort_order=2, is_active=True, is_featured=True,
        )

    return site


def slug_from_email(email):
    """Generate a candidate username from an email address.
    Used by the public signup flow — the trainer can rename later."""
    base = (email or "").split("@")[0] or "client"
    return slugify(base)[:120] or "client"
