"""
Phase 10 — GymFlow Hub.

The hub is the new front page of `/dashboard/`. It exists to give the PT
a single "is everything OK?" view — and to wow them on first impression.

Structure (top → bottom):
    1. Hero greeting     — time-of-day greeting + animated headline stats
    2. Setup checklist   — only rendered until all 6 steps are done
    3. Needs attention   — derived from _next_actions in activity_views
    4. Activity preview  — last 8 events from _collect_events
    5. Workspace grid    — 6 tiles with live badges
    6. Tips + What's new — markdown-driven cards

Workouts has moved from `/dashboard/` → `/dashboard/workouts/`. The
old `trainer-dashboard-home` URL name is preserved (now points at the
new workouts URL) so existing templates that `{% url 'trainer-dashboard-home' %}`
keep working without a 50-file edit.
"""
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from .dashboard_helpers import (
    trainer_required,
    dashboard_context,
    get_trainer_clients,
)
from .dashboard_activity_views import _collect_events
from .models import User
from apps.workouts.models import WorkoutPlan
from apps.nutrition.models import NutritionPlan
from apps.progress.models import CheckInForm, CheckInQuestion, CheckInSubmission


# ----------------------------------------------------------------------
# Setup checklist — drives the "are you set up yet?" card on the hub.
# Each step is a dict the template renders; `done` flips the checkmark
# and dims the row. The card collapses entirely once all 6 are true.
# ----------------------------------------------------------------------
def _setup_checklist(trainer):
    """Return a list of 6 setup-step dicts and the overall done count."""
    # Detection queries — all single-row .exists() calls so this stays
    # cheap even on chatty hub renders.
    has_clients = User.objects.filter(
        role=User.CLIENT,
        client_profile__trainer=trainer.trainer_profile,
    ).exists()

    has_workouts = WorkoutPlan.objects.filter(user=trainer).exists()
    has_nutrition = NutritionPlan.objects.filter(user=trainer).exists()

    has_checkin_questions = CheckInQuestion.objects.filter(
        form__user=trainer
    ).exists()

    # "Site customised" = the trainer has visited the Site editor at
    # least once (which materialises SiteSection rows via bootstrap).
    # Lazy import so this view doesn't pull apps.sites at module load.
    from apps.sites.models import SiteSection, PricingPlan
    has_site = SiteSection.objects.filter(
        site__trainer=trainer.trainer_profile
    ).exists()

    has_pricing = PricingPlan.objects.filter(
        trainer=trainer.trainer_profile
    ).exists()

    steps = [
        {
            "n": 1,
            "title": "Customise your public site",
            "sub":   "Hero, about, services, pricing — the front door for clients.",
            "done":  has_site,
            "cta":   "Open Site builder",
            "url":   reverse("trainer-site-page"),
        },
        {
            "n": 2,
            "title": "Add your first client",
            "sub":   "Or share your signup link — clients can apply themselves.",
            "done":  has_clients,
            "cta":   "Open Clients",
            "url":   reverse("trainer-dashboard"),
        },
        {
            "n": 3,
            "title": "Build your first workout plan",
            "sub":   "Drag-drop exercises into days, then assign to a client.",
            "done":  has_workouts,
            "cta":   "Open Workouts",
            "url":   reverse("trainer-dashboard-home"),
        },
        {
            "n": 4,
            "title": "Build your first nutrition plan",
            "sub":   "Macros + meals from the food library.",
            "done":  has_nutrition,
            "cta":   "Open Nutrition",
            "url":   reverse("trainer-nutrition-plans-page"),
        },
        {
            "n": 5,
            "title": "Configure your check-in forms",
            "sub":   "Onboarding, daily, and weekly questions for clients.",
            "done":  has_checkin_questions,
            "cta":   "Open Check-Ins",
            "url":   reverse("trainer-checkin-forms-page"),
        },
        {
            "n": 6,
            "title": "Set your pricing tiers",
            "sub":   "Plans clients see on your public site.",
            "done":  has_pricing,
            "cta":   "Open Settings",
            "url":   reverse("trainer-settings-page"),
        },
    ]
    done_count = sum(1 for s in steps if s["done"])
    return steps, done_count


# ----------------------------------------------------------------------
# Headline + action chips — single source of truth.
#
# The nav-bar badge (`action_needed_count` from dashboard_helpers) counts
# (clients × missing-resources) — so 2 clients each missing a workout
# plan + 1 missing a nutrition plan = 3. The hub's hero chips, the hub's
# Clients-tile badge, and the nav badge all feed from here so the
# numbers always match.
# ----------------------------------------------------------------------
def _headline_and_actions(trainer, clients):
    """Returns (headline, action_chips, action_total).

    headline: just the big "active clients" number.
    action_chips: list of small inline chips for the hero, one per
                  category. Empty list = "all clear".
    action_total: sum of weights across chips. Matches the nav badge.
    """
    active_clients = clients.count() if hasattr(clients, "count") else len(clients)

    chips = []

    missing_workout = User.objects.filter(
        role=User.CLIENT,
        client_profile__trainer=trainer.trainer_profile,
        client_profile__assigned_workout_plan__isnull=True,
    ).count()
    if missing_workout:
        chips.append({
            "icon":   "⌁",
            "title":  f"{missing_workout} client{'s' if missing_workout != 1 else ''}"
                      f" need{'s' if missing_workout == 1 else ''} a workout plan",
            "url":    reverse("trainer-dashboard"),
            "weight": missing_workout,
        })

    missing_nutrition = User.objects.filter(
        role=User.CLIENT,
        client_profile__trainer=trainer.trainer_profile,
        client_profile__assigned_nutrition_plan__isnull=True,
    ).count()
    if missing_nutrition:
        chips.append({
            "icon":   "◌",
            "title":  f"{missing_nutrition} client{'s' if missing_nutrition != 1 else ''}"
                      f" need{'s' if missing_nutrition == 1 else ''} a nutrition plan",
            "url":    reverse("trainer-dashboard"),
            "weight": missing_nutrition,
        })

    pending_subs = CheckInSubmission.objects.filter(
        form__user=trainer,
        status=CheckInSubmission.STATUS_SUBMITTED,
    ).count()
    if pending_subs:
        chips.append({
            "icon":   "☑",
            "title":  f"{pending_subs} check-in{'s' if pending_subs != 1 else ''} to review",
            "url":    reverse("trainer-activity-page"),
            "weight": pending_subs,
        })

    headline = {"active_clients": active_clients}
    total = sum(c["weight"] for c in chips)
    return headline, chips, total


# ----------------------------------------------------------------------
# Workspace grid — 6 tiles with live badges so the PT sees status at a
# glance without clicking through. The badge is the # of items in that
# workspace that need their attention right now (or a friendly count).
# ----------------------------------------------------------------------
def _workspace_tiles(trainer, clients, headline, action_total):
    # Plans + form counts — single .count() each, cheap.
    workout_count = WorkoutPlan.objects.filter(user=trainer).count()
    nutrition_count = NutritionPlan.objects.filter(user=trainer).count()
    form_count = CheckInForm.objects.filter(user=trainer).exclude(
        questions__isnull=True
    ).distinct().count()

    pending_subs = CheckInSubmission.objects.filter(
        form__user=trainer,
        status=CheckInSubmission.STATUS_SUBMITTED,
    ).count()

    # Per-resource missing counts so each tile can show its own badge.
    missing_workout = User.objects.filter(
        role=User.CLIENT,
        client_profile__trainer=trainer.trainer_profile,
        client_profile__assigned_workout_plan__isnull=True,
    ).count()
    missing_nutrition = User.objects.filter(
        role=User.CLIENT,
        client_profile__trainer=trainer.trainer_profile,
        client_profile__assigned_nutrition_plan__isnull=True,
    ).count()

    return [
        {
            "key":   "workouts",
            "icon":  "⌁",
            "name":  "Workouts",
            "sub":   f"{workout_count} plan{'s' if workout_count != 1 else ''}",
            "url":   reverse("trainer-dashboard-home"),
            "badge": missing_workout if missing_workout else None,
            "tone":  "amber" if missing_workout else "lime",
        },
        {
            "key":   "clients",
            "icon":  "◯",
            "name":  "Clients",
            "sub":   f"{headline['active_clients']} on roster",
            "url":   reverse("trainer-dashboard"),
            # Badge matches the top-nav badge so the two numbers never disagree.
            "badge": action_total if action_total else None,
            "tone":  "amber" if action_total else "lime",
        },
        {
            "key":   "nutrition",
            "icon":  "◌",
            "name":  "Nutrition",
            "sub":   f"{nutrition_count} plan{'s' if nutrition_count != 1 else ''}",
            "url":   reverse("trainer-nutrition-plans-page"),
            "badge": missing_nutrition if missing_nutrition else None,
            "tone":  "amber" if missing_nutrition else "lime",
        },
        {
            "key":   "checkins",
            "icon":  "☑",
            "name":  "Check-Ins",
            "sub":   f"{form_count} form{'s' if form_count != 1 else ''}",
            "url":   reverse("trainer-checkin-forms-page"),
            "badge": pending_subs if pending_subs else None,
            "tone":  "amber" if pending_subs else "lime",
        },
        {
            "key":   "site",
            "icon":  "◧",
            "name":  "Site",
            "sub":   "Public landing page",
            "url":   reverse("trainer-site-page"),
            "badge": None,
            "tone":  "lime",
        },
        {
            "key":   "activity",
            "icon":  "◔",
            "name":  "Activity",
            "sub":   "All your client events",
            "url":   reverse("trainer-activity-page"),
            "badge": None,
            "tone":  "lime",
        },
    ]


# ----------------------------------------------------------------------
# Static tips deck — markdown-driven later. For now, three friendly
# coaching-business cards that show under "Coaching tips".
# ----------------------------------------------------------------------
TIPS = [
    {
        "icon":  "✦",
        "title": "Share your signup link daily",
        "body":  "Drop your gymflow.coach URL in your IG bio + every story. "
                 "Even one new application a week compounds.",
    },
    {
        "icon":  "◐",
        "title": "Reply to check-ins within 24h",
        "body":  "The biggest churn driver in coaching is feeling unheard. "
                 "Aim for same-day replies on weekly forms.",
    },
    {
        "icon":  "◇",
        "title": "Update plans monthly, not weekly",
        "body":  "Clients need consistency to hit progressive overload. "
                 "Tweak rep ranges every 4–6 weeks, not constantly.",
    },
]


def _greeting(now):
    """Time-of-day greeting based on local hour."""
    hour = timezone.localtime(now).hour
    if hour < 5:
        return "You're up late"
    if hour < 12:
        return "Good morning"
    if hour < 17:
        return "Good afternoon"
    if hour < 22:
        return "Good evening"
    return "Burning the midnight oil"


@login_required
def trainer_hub_page(request):
    """GET /dashboard/ — the GymFlow Hub."""
    if not trainer_required(request):
        return redirect("landing-page")

    context = dashboard_context(request, "Hub")
    trainer = request.user
    clients = context["clients"]

    # Hero
    now = timezone.now()
    context["hub_greeting"] = _greeting(now)
    context["hub_first_name"] = (
        trainer.first_name
        or context["trainer_profile"].business_name
        or trainer.username
    )

    # Headline + unified action chips (one source of truth for nav badge,
    # hero chip-list, and Clients tile badge).
    headline, action_chips, action_total = _headline_and_actions(trainer, clients)
    context["hub_headline"] = headline
    context["hub_action_chips"] = action_chips
    context["hub_action_total"] = action_total

    steps, done_count = _setup_checklist(trainer)
    context["hub_setup_steps"] = steps
    context["hub_setup_done"] = done_count
    context["hub_setup_total"] = len(steps)
    context["hub_setup_complete"] = (done_count == len(steps))
    # Percent for the progress ring — rounded to nearest int for clean SVG.
    context["hub_setup_percent"] = int(round(100 * done_count / len(steps)))

    context["hub_tiles"] = _workspace_tiles(trainer, clients, headline, action_total)

    # Recent events — last 8 across "all" kinds, last 30 days.
    since = timezone.now() - timedelta(days=30)
    events = _collect_events(trainer, kind="all", since=since, client_filter_id=0)
    context["hub_recent_events"] = events[:8]

    context["hub_tips"] = TIPS

    # Tiny "what's new" line — manual for now, will read from a model later.
    context["hub_whats_new"] = {
        "title":   "Pricing tiers + Stripe coming soon",
        "body":    "Set monthly/weekly/yearly plans on your Site. "
                   "Live Stripe payments are next on the roadmap.",
        "url":     reverse("trainer-settings-page"),
        "url_cta": "Set up tiers →",
    }

    return render(request, "dashboard/dashboard_hub.html", context)
