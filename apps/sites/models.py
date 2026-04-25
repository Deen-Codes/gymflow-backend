"""
Phase 7 — PT site models.

Each trainer gets one TrainerSite, which holds publishing state + brand
config + a list of SiteSection rows (the widgets). Sections have a
type (Hero, About, Services, Testimonials, Onboarding, Footer), an
order, and a JSON `content` blob whose shape depends on the type.

The site is auto-bootstrapped with the 6 default sections on first
visit (lazy `get_or_create` from the editor view).
"""
from django.db import models

from apps.users.models import TrainerProfile


class TrainerSite(models.Model):
    """One PT's public landing page."""

    trainer = models.OneToOneField(
        TrainerProfile,
        on_delete=models.CASCADE,
        related_name="site",
    )

    # Publish state — when False, /p/<slug>/ returns 404 to the public
    # so trainers can edit privately before going live.
    is_published = models.BooleanField(default=False)

    # Brand colour (hex). Falls back to the GymFlow accent if blank.
    brand_color = models.CharField(max_length=7, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Site for {self.trainer}"


class SiteSection(models.Model):
    """One widget on a PT's site.

    `content` is a JSON blob whose shape depends on `section_type`:
        hero         — {headline, subheadline, image_url, primary_cta, secondary_cta}
        about        — {heading, body, photo_url}
        services     — {heading, items: [{title, description}, ...]}
        testimonials — {heading, items: [{quote, author}, ...]}
        onboarding   — {heading, lede, submit_label}  (form rendered from
                       the trainer's actual onboarding CheckInForm)
        footer       — {tagline, instagram, twitter, email}

    Onboarding + Footer are mandatory (`is_required=True`) — they can be
    hidden in the editor (won't render) but never deleted.
    """

    HERO = "hero"
    ABOUT = "about"
    SERVICES = "services"
    PRICING = "pricing"        # Phase 7.7 — tier cards drive the Pay flow
    TESTIMONIALS = "testimonials"
    ONBOARDING = "onboarding"
    FOOTER = "footer"

    SECTION_TYPE_CHOICES = [
        (HERO,         "Hero"),
        (ABOUT,        "About"),
        (SERVICES,     "Services"),
        (PRICING,      "Pricing"),
        (TESTIMONIALS, "Testimonials"),
        (ONBOARDING,   "Onboarding form"),
        (FOOTER,       "Footer"),
    ]

    # Default canonical order on bootstrap. Pricing slots between
    # Services and Testimonials — that's where prospects expect to
    # decide before reading social proof.
    DEFAULT_ORDER = [HERO, ABOUT, SERVICES, PRICING, TESTIMONIALS, ONBOARDING, FOOTER]
    REQUIRED_TYPES = (ONBOARDING, FOOTER)

    site = models.ForeignKey(
        TrainerSite,
        on_delete=models.CASCADE,
        related_name="sections",
    )
    section_type = models.CharField(max_length=32, choices=SECTION_TYPE_CHOICES)
    order = models.IntegerField(default=0)
    is_visible = models.BooleanField(default=True)
    is_required = models.BooleanField(default=False)
    content = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.get_section_type_display()} (#{self.order})"


class PricingPlan(models.Model):
    """One pricing tier the trainer offers (e.g. "Lean Bulk · £150/mo").
    Surfaced on the public Site as "Subscribe" cards. Phase 7.7 stores
    the tiers + UX; Phase 7.7.1 wires Stripe Connect so the Subscribe
    button actually charges."""

    INTERVAL_MONTHLY = "monthly"
    INTERVAL_WEEKLY = "weekly"
    INTERVAL_YEARLY = "yearly"
    INTERVAL_ONESHOT = "oneshot"  # one-off fee
    INTERVAL_CHOICES = [
        (INTERVAL_MONTHLY, "per month"),
        (INTERVAL_WEEKLY,  "per week"),
        (INTERVAL_YEARLY,  "per year"),
        (INTERVAL_ONESHOT, "one-time"),
    ]

    trainer = models.ForeignKey(
        TrainerProfile,
        on_delete=models.CASCADE,
        related_name="pricing_plans",
    )
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, default="")

    # Prices stored as integer pennies/cents to avoid float headaches.
    # Display layer divides by 100 with a 2-dp format string.
    price_pennies = models.IntegerField(default=0)
    currency = models.CharField(max_length=3, default="GBP")
    interval = models.CharField(
        max_length=20, choices=INTERVAL_CHOICES, default=INTERVAL_MONTHLY,
    )

    # Order of display on the public Pricing widget.
    sort_order = models.IntegerField(default=0)
    # Trainer can soft-disable a tier without deleting it.
    is_active = models.BooleanField(default=True)
    # Highlight one tier as "Most popular" — purely cosmetic.
    is_featured = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.name} ({self.price_pennies}p / {self.interval})"

    @property
    def price_display(self):
        """Format pence/cents as £X.YY-style, dropping .00 when round."""
        major = self.price_pennies / 100.0
        symbol = {"GBP": "£", "USD": "$", "EUR": "€"}.get(self.currency, self.currency + " ")
        if major == int(major):
            return f"{symbol}{int(major)}"
        return f"{symbol}{major:.2f}"


class PublicSignup(models.Model):
    """One submission of the public onboarding form on a trainer's site.

    We keep the raw JSON payload so the trainer can review it later even
    if their onboarding form changes shape. The created `client_user`
    references the User account we minted from the signup."""

    site = models.ForeignKey(
        TrainerSite,
        on_delete=models.CASCADE,
        related_name="signups",
    )
    client_user = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="public_signup",
    )
    full_name = models.CharField(max_length=255)
    email = models.EmailField()
    raw_answers = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.full_name} → {self.site.trainer}"
