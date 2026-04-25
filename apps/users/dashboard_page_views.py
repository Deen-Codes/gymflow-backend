"""
Top-level dashboard pages.

Restructure v2:
    /dashboard/      → Workouts workspace (the builder), front page of the app.
    /dashboard/activity/ → Activity feed (Phase 5 scaffold).
    /dashboard/settings/ → Settings (Phase 6 scaffold).

The launcher / "open a workspace" placeholder has been removed entirely;
the home view now boots straight into Workouts with the most-recently
edited plan auto-selected. If the trainer has no plans yet, the workouts
template renders its own first-run create-a-plan card.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Max
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils.text import slugify

from .dashboard_helpers import trainer_required, dashboard_context
from .dashboard_workout_page_views import _render_workouts_workspace
from .models import TrainerProfile


@login_required
def trainer_dashboard_home(request):
    """
    Front page of the dashboard.

    Renders the Workouts workspace with the most-recently created plan
    selected. If no plan exists, the template shows a first-run card.
    """
    if not trainer_required(request):
        return redirect("landing-page")

    return _render_workouts_workspace(request, plan_id=None)


@login_required
def trainer_settings_page(request):
    """Phase 6 — Settings workspace.

    Real sections (live now): business profile editing, password change,
    pricing tiers, logout. Scaffolded sections: theme, text size,
    notifications, integrations, account deletion.
    """
    if not trainer_required(request):
        return redirect("landing-page")
    context = dashboard_context(request, "Settings")

    # Pull pricing tiers for the trainer (Phase 7.7). Lazy import so
    # this view doesn't pull apps.sites at module load.
    from apps.sites.models import PricingPlan
    context["pricing_plans"] = list(
        PricingPlan.objects
        .filter(trainer=request.user.trainer_profile)
        .order_by("sort_order", "id")
    )
    context["pricing_intervals"] = PricingPlan.INTERVAL_CHOICES

    return render(request, "dashboard/dashboard_settings.html", context)


@login_required
def dashboard_pricing_save(request):
    """POST /dashboard/settings/pricing/save/

    Add or update a single pricing tier. Body: {plan_id?, name,
    description, price, interval, is_active, is_featured}. Empty
    plan_id creates a new tier; populated plan_id updates that one.
    """
    if not trainer_required(request):
        return redirect("landing-page")
    if request.method != "POST":
        return redirect("trainer-settings-page")

    from apps.sites.models import PricingPlan
    profile = request.user.trainer_profile

    plan_id = (request.POST.get("plan_id") or "").strip()
    name = (request.POST.get("name") or "").strip()[:120]
    description = (request.POST.get("description") or "").strip()
    price_raw = (request.POST.get("price") or "0").strip()
    interval = (request.POST.get("interval") or "monthly").strip()
    is_active = bool(request.POST.get("is_active"))
    is_featured = bool(request.POST.get("is_featured"))

    if not name:
        messages.error(request, "Tier name is required.")
        return redirect("trainer-settings-page")

    try:
        price_pennies = int(round(float(price_raw) * 100))
    except (TypeError, ValueError):
        messages.error(request, "Price must be a number (e.g. 150 or 99.50).")
        return redirect("trainer-settings-page")

    valid_intervals = {slug for slug, _ in PricingPlan.INTERVAL_CHOICES}
    if interval not in valid_intervals:
        interval = PricingPlan.INTERVAL_MONTHLY

    fields = dict(
        name=name,
        description=description,
        price_pennies=max(0, price_pennies),
        interval=interval,
        is_active=is_active,
        is_featured=is_featured,
    )

    if plan_id:
        plan = (
            PricingPlan.objects
            .filter(pk=plan_id, trainer=profile)
            .first()
        )
        if not plan:
            messages.error(request, "That tier doesn't exist.")
            return redirect("trainer-settings-page")
        for k, v in fields.items():
            setattr(plan, k, v)
        plan.save()
        messages.success(request, f'Tier "{plan.name}" saved.')
    else:
        # New tier — append at the end of the order.
        last_order = (
            PricingPlan.objects
            .filter(trainer=profile)
            .aggregate(m=Max("sort_order"))["m"]
            or 0
        )
        plan = PricingPlan.objects.create(
            trainer=profile, sort_order=last_order + 1, **fields,
        )
        messages.success(request, f'Tier "{plan.name}" added.')

    return redirect("trainer-settings-page")


@login_required
def dashboard_pricing_delete(request, plan_id):
    """POST /dashboard/settings/pricing/<id>/delete/"""
    if not trainer_required(request):
        return redirect("landing-page")
    if request.method != "POST":
        return redirect("trainer-settings-page")

    from apps.sites.models import PricingPlan
    plan = PricingPlan.objects.filter(
        pk=plan_id, trainer=request.user.trainer_profile,
    ).first()
    if plan:
        name = plan.name
        plan.delete()
        messages.success(request, f'Tier "{name}" deleted.')
    return redirect("trainer-settings-page")


@login_required
def dashboard_update_profile(request):
    """POST /dashboard/settings/profile/

    Updates the trainer's User (first/last name, email) and
    TrainerProfile (business_name). Username stays locked — it's how
    the trainer logs in.
    """
    if not trainer_required(request):
        return redirect("landing-page")
    if request.method != "POST":
        return redirect("trainer-settings-page")

    user = request.user
    profile = user.trainer_profile

    # Light validation — strip and trim. Email is checked by Django's
    # built-in EmailField semantics if we promote this to a Form later.
    first_name = (request.POST.get("first_name") or "").strip()[:150]
    last_name = (request.POST.get("last_name") or "").strip()[:150]
    email = (request.POST.get("email") or "").strip()[:254]
    business_name = (request.POST.get("business_name") or "").strip()[:255]

    # Capture the old values so we can propagate changes to the PT's
    # landing-page section content (hero headline, footer tagline, etc.
    # were seeded with the old name and would otherwise stay stale).
    old_business_name = profile.business_name
    old_email = user.email

    if not email:
        messages.error(request, "Email is required.")
        return redirect("trainer-settings-page")

    # Slug edit: auto-slugify whatever the trainer typed (so "Jared
    # Coaching" → "jared-coaching"), then enforce uniqueness across
    # the trainer table. Empty input keeps the existing slug.
    raw_slug = (request.POST.get("slug") or "").strip()
    new_slug = slugify(raw_slug)[:50] if raw_slug else profile.slug

    if new_slug != profile.slug:
        clash = (
            TrainerProfile.objects
            .filter(slug=new_slug)
            .exclude(pk=profile.pk)
            .exists()
        )
        if not new_slug:
            messages.error(request, "Slug can't be empty after sanitising — try plain letters / numbers / hyphens.")
            return redirect("trainer-settings-page")
        if clash:
            messages.error(request, f'The slug "{new_slug}" is already taken — pick a different one.')
            return redirect("trainer-settings-page")
        profile.slug = new_slug

    user.first_name = first_name
    user.last_name = last_name
    user.email = email
    user.save(update_fields=["first_name", "last_name", "email"])

    profile.business_name = business_name
    profile.save(update_fields=["business_name", "slug"])

    # Propagate name + email changes into any landing-page section
    # content that contained the old values. Trainer-customised text
    # that no longer contains the old string is left alone.
    _propagate_profile_changes_to_site(
        profile,
        replacements=[
            (old_business_name, business_name),
            (old_email, email),
        ],
    )

    messages.success(request, "Profile updated.")
    return redirect("trainer-settings-page")


def _walk_replace(value, old, new):
    """Recursively walk a JSON-ish value, replacing `old` with `new`
    in any string. Lists and dicts are walked element-by-element."""
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, list):
        return [_walk_replace(v, old, new) for v in value]
    if isinstance(value, dict):
        return {k: _walk_replace(v, old, new) for k, v in value.items()}
    return value


def _propagate_profile_changes_to_site(profile, replacements):
    """Rewrite any landing-page section content that still contains an
    old field value. Skips no-ops (empty old, identical old/new)."""
    # Local import — avoid a circular import (apps.sites depends on
    # apps.users, which is the package this view lives in).
    try:
        from apps.sites.models import TrainerSite
    except ImportError:
        return

    site = TrainerSite.objects.filter(trainer=profile).first()
    if not site:
        return

    for section in site.sections.all():
        new_content = section.content or {}
        for old, new in replacements:
            if not old or old == new:
                continue
            new_content = _walk_replace(new_content, old, new)
        if new_content != section.content:
            section.content = new_content
            section.save(update_fields=["content"])


@login_required
def dashboard_check_slug(request):
    """GET /dashboard/settings/check-slug/?slug=foo

    Returns:
        {
          sanitized:  "foo-bar"   — what the slug becomes after slugify
          available:  bool        — true if no one else holds it
          is_self:    bool        — true if it's the trainer's current slug
          suggestions: ["foo-2", "foo-3", ...]   — only when not available
        }
    Used by the Settings page to give live feedback as the trainer
    edits their URL slug. No DB writes — purely read.
    """
    if not trainer_required(request):
        return JsonResponse({"detail": "forbidden"}, status=403)

    raw = (request.GET.get("slug") or "").strip()
    sanitized = slugify(raw)[:50]

    if not sanitized:
        return JsonResponse({
            "sanitized": "",
            "available": False,
            "is_self": False,
            "suggestions": [],
            "reason": "empty",
        })

    profile = request.user.trainer_profile

    if sanitized == profile.slug:
        return JsonResponse({
            "sanitized": sanitized,
            "available": True,
            "is_self": True,
            "suggestions": [],
        })

    taken = TrainerProfile.objects.filter(slug=sanitized).exclude(pk=profile.pk).exists()

    suggestions = []
    if taken:
        # Try numeric suffixes first, then a couple of letter variants.
        for suffix in ("2", "3", "coach", "official", "online"):
            cand = f"{sanitized}-{suffix}"[:50]
            if not TrainerProfile.objects.filter(slug=cand).exclude(pk=profile.pk).exists():
                suggestions.append(cand)
            if len(suggestions) >= 3:
                break

    return JsonResponse({
        "sanitized": sanitized,
        "available": not taken,
        "is_self": False,
        "suggestions": suggestions,
    })


@login_required
def dashboard_delete_account(request):
    """POST /dashboard/settings/delete-account/

    Hard-delete the trainer account. Cascades to TrainerProfile,
    every WorkoutPlan / NutritionPlan / CheckInForm they own, and
    their client roster. Irreversible.
    """
    if not trainer_required(request):
        return redirect("landing-page")
    if request.method != "POST":
        return redirect("trainer-settings-page")

    confirm = (request.POST.get("confirm") or "").strip().lower()
    if confirm != "delete":
        messages.error(request, 'Type "delete" to confirm account deletion.')
        return redirect("trainer-settings-page")

    user = request.user
    user.delete()
    return redirect("landing-page")


# `trainer_activity_page` lives in dashboard_activity_views.py since
# Phase 5 — too much harvesting logic to keep in this aggregator file.
