"""
Activity workspace — coaching feed across the trainer's whole roster.

Aggregates events from every model that holds a useful timestamp, sorts
chronologically, groups by day, and renders the Activity workspace.

Trainer-side events (live now):
    • client added            — User.date_joined where role=CLIENT
    • workout plan created    — WorkoutPlan.created_at
    • nutrition plan created  — NutritionPlan.created_at
    • check-in form built     — CheckInForm.created_at
    • food added to library   — FoodLibraryItem.created_at
    • exercise added to lib   — ExerciseLibraryItem.created_at

Client-side events (Phase 7, when iOS submits):
    • workout completed       — WorkoutSession.completed_at
    • check-in submitted      — CheckInSubmission.submitted_at
"""
from collections import OrderedDict
from datetime import timedelta
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from .dashboard_helpers import trainer_required, dashboard_context, get_trainer_clients
from .models import User
from apps.workouts.models import (
    WorkoutPlan,
    WorkoutSession,
    ExerciseLibraryItem,
)
from apps.nutrition.models import (
    NutritionPlan,
    FoodLibraryItem,
)
from apps.progress.models import (
    CheckInForm,
    CheckInSubmission,
)


# Filter taxonomy — kept on the server so the JS doesn't have to know
# about model names. Each pill in the UI passes one of these slugs.
EVENT_KIND_CHOICES = [
    ("all",       "All"),
    ("clients",   "Clients"),
    ("plans",     "Plans"),
    ("nutrition", "Nutrition"),
    ("forms",     "Check-Ins"),
    ("library",   "Library"),
    ("logged",    "Workouts logged"),
]

TIME_RANGE_CHOICES = [
    ("7",   "Last 7 days"),
    ("30",  "Last 30 days"),
    ("90",  "Last 90 days"),
    ("0",   "All time"),
]


def _since(days):
    """Return the UTC cutoff for a `days` window. Zero / None = no cutoff."""
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 30
    if days <= 0:
        return None
    return timezone.now() - timedelta(days=days)


def _client_url(client_id):
    return reverse("trainer-client-detail", kwargs={"client_id": client_id})


def _workouts_url():
    return reverse("trainer-dashboard-home")


def _workout_plan_url(plan_id):
    return reverse("trainer-workout-plan-detail", kwargs={"plan_id": plan_id})


def _nutrition_plan_url(plan_id):
    return reverse("trainer-nutrition-plan-detail", kwargs={"plan_id": plan_id})


def _checkin_form_url(form_id):
    return reverse("trainer-checkin-form-detail", kwargs={"form_id": form_id})


# ---------------------------------------------------------------
# Event harvesters — each returns a list of dicts with the same shape:
#   {
#     kind: "clients" | "plans" | ...,
#     icon: "◯" | "⌁" | ...,
#     title: str,
#     subtitle: str,
#     timestamp: datetime,
#     link: url-or-None,
#     client_id: int-or-None,   ← used for "Filter by client" pill
#   }
# ---------------------------------------------------------------
def _client_events(trainer, since, client_filter_id):
    qs = User.objects.filter(
        role=User.CLIENT,
        client_profile__trainer=trainer.trainer_profile,
    ).order_by("-date_joined")
    if since is not None:
        qs = qs.filter(date_joined__gte=since)
    if client_filter_id:
        qs = qs.filter(id=client_filter_id)

    out = []
    for c in qs:
        out.append({
            "kind": "clients",
            "icon": "◯",
            "title": f"{c.username} joined your roster",
            "subtitle": c.email or "—",
            "timestamp": c.date_joined,
            "link": _client_url(c.id),
            "client_id": c.id,
        })
    return out


def _workout_plan_events(trainer, since, client_filter_id):
    qs = WorkoutPlan.objects.filter(user=trainer).order_by("-created_at", "-id")
    if since is not None:
        qs = qs.filter(created_at__gte=since)
    if client_filter_id:
        qs = qs.filter(client_id=client_filter_id)

    out = []
    for p in qs:
        if p.created_at is None:
            # Old rows from before the timestamp migration — skip from
            # the feed rather than showing them as "right now".
            continue
        if p.is_template:
            title = f'Workout plan "{p.name}" created'
            subtitle = f"{p.days.count()} day{'s' if p.days.count() != 1 else ''} · template"
            client_id = None
        else:
            client_label = p.client.username if p.client_id else "a client"
            title = f'Client-specific workout plan "{p.name}" built for {client_label}'
            subtitle = f"{p.days.count()} day{'s' if p.days.count() != 1 else ''}"
            client_id = p.client_id
        out.append({
            "kind": "plans",
            "icon": "⌁",
            "title": title,
            "subtitle": subtitle,
            "timestamp": p.created_at,
            "link": _workout_plan_url(p.id),
            "client_id": client_id,
        })
    return out


def _nutrition_plan_events(trainer, since, client_filter_id):
    qs = NutritionPlan.objects.filter(user=trainer).order_by("-created_at", "-id")
    if since is not None:
        qs = qs.filter(created_at__gte=since)
    if client_filter_id:
        qs = qs.filter(client_id=client_filter_id)

    out = []
    for p in qs:
        if p.created_at is None:
            continue
        if p.is_template:
            title = f'Nutrition plan "{p.name}" created'
            subtitle = f"{p.calories_target} kcal · template"
            client_id = None
        else:
            client_label = p.client.username if p.client_id else "a client"
            title = f'Client-specific nutrition plan "{p.name}" built for {client_label}'
            subtitle = f"{p.calories_target} kcal"
            client_id = p.client_id
        out.append({
            "kind": "nutrition",
            "icon": "◌",
            "title": title,
            "subtitle": subtitle,
            "timestamp": p.created_at,
            "link": _nutrition_plan_url(p.id),
            "client_id": client_id,
        })
    return out


def _checkin_form_events(trainer, since, client_filter_id):
    if client_filter_id:
        # Forms aren't per-client, so filtering by client hides them.
        return []
    qs = CheckInForm.objects.filter(user=trainer).order_by("-created_at")
    if since is not None:
        qs = qs.filter(created_at__gte=since)

    out = []
    for f in qs:
        out.append({
            "kind": "forms",
            "icon": "☑",
            "title": f'{f.get_form_type_display()} form "{f.name}" built',
            "subtitle": f"{f.questions.count()} question{'s' if f.questions.count() != 1 else ''}",
            "timestamp": f.created_at,
            "link": _checkin_form_url(f.id),
            "client_id": None,
        })
    return out


def _library_events(trainer, since, client_filter_id):
    if client_filter_id:
        return []
    out = []
    fqs = FoodLibraryItem.objects.filter(user=trainer).order_by("-created_at")
    eqs = ExerciseLibraryItem.objects.filter(user=trainer).order_by("-created_at")
    if since is not None:
        fqs = fqs.filter(created_at__gte=since)
        eqs = eqs.filter(created_at__gte=since)
    for f in fqs:
        out.append({
            "kind": "library",
            "icon": "◑",
            "title": f'Added "{f.name}" to your food library',
            "subtitle": f"{int(f.calories or 0)} kcal · /{int(f.reference_grams or 100)}g",
            "timestamp": f.created_at,
            "link": reverse("trainer-nutrition-plans-page"),
            "client_id": None,
        })
    for e in eqs:
        out.append({
            "kind": "library",
            "icon": "⌁",
            "title": f'Added "{e.name}" to your exercise library',
            "subtitle": (e.muscle_group or "—") + (" · " + e.equipment if e.equipment else ""),
            "timestamp": e.created_at,
            "link": _workouts_url(),
            "client_id": None,
        })
    return out


def _workout_logged_events(trainer, since, client_filter_id):
    """Client-side events. Empty until iOS starts writing WorkoutSession
    rows — but the harvester is wired so the feed lights up automatically
    when that traffic begins."""
    qs = WorkoutSession.objects.filter(
        user__client_profile__trainer=trainer.trainer_profile,
        is_complete=True,
    ).select_related("user", "workout_day__plan").order_by("-completed_at")
    if since is not None:
        qs = qs.filter(completed_at__gte=since)
    if client_filter_id:
        qs = qs.filter(user_id=client_filter_id)

    out = []
    for s in qs[:100]:
        client_label = s.user.username
        day_title = s.workout_day.title if s.workout_day_id else "a workout"
        plan_name = s.workout_day.plan.name if s.workout_day_id else ""
        out.append({
            "kind": "logged",
            "icon": "✓",
            "title": f'{client_label} completed "{day_title}"',
            "subtitle": (plan_name or "Workout logged"),
            "timestamp": s.completed_at,
            "link": _client_url(s.user_id),
            "client_id": s.user_id,
        })
    return out


def _checkin_submission_events(trainer, since, client_filter_id):
    """Client check-in submissions — empty until iOS posts them."""
    qs = CheckInSubmission.objects.filter(
        form__user=trainer,
        status=CheckInSubmission.STATUS_SUBMITTED,
    ).select_related("form", "client").order_by("-submitted_at")
    if since is not None:
        qs = qs.filter(submitted_at__gte=since)
    if client_filter_id:
        qs = qs.filter(client_id=client_filter_id)

    out = []
    for s in qs[:100]:
        out.append({
            "kind": "forms",
            "icon": "☑",
            "title": f"{s.client.username} submitted {s.form.get_form_type_display().lower()} check-in",
            "subtitle": s.form.name,
            "timestamp": s.submitted_at,
            "link": _client_url(s.client_id),
            "client_id": s.client_id,
        })
    return out


HARVESTERS = {
    "clients":   _client_events,
    "plans":     _workout_plan_events,
    "nutrition": _nutrition_plan_events,
    "forms":     [_checkin_form_events, _checkin_submission_events],
    "library":   _library_events,
    "logged":    _workout_logged_events,
}


def _collect_events(trainer, kind, since, client_filter_id):
    if kind == "all":
        sources = []
        for v in HARVESTERS.values():
            sources.extend(v if isinstance(v, list) else [v])
    else:
        v = HARVESTERS.get(kind, [])
        sources = v if isinstance(v, list) else [v]

    events = []
    for fn in sources:
        events.extend(fn(trainer, since, client_filter_id))
    events.sort(key=lambda e: e["timestamp"], reverse=True)
    return events


# ---------------------------------------------------------------
# Day grouping ("Today" / "Yesterday" / "This week" / etc.)
# ---------------------------------------------------------------
def _bucket_label(event_date, today):
    if event_date == today:
        return "Today"
    if event_date == today - timedelta(days=1):
        return "Yesterday"
    if event_date >= today - timedelta(days=7):
        return "This week"
    if event_date >= today - timedelta(days=30):
        return "This month"
    return event_date.strftime("%B %Y")


def _group_by_bucket(events):
    today = timezone.localdate()
    grouped = OrderedDict()
    for e in events:
        local = timezone.localtime(e["timestamp"]).date() if timezone.is_aware(e["timestamp"]) else e["timestamp"].date()
        label = _bucket_label(local, today)
        grouped.setdefault(label, []).append(e)
    return grouped


# ---------------------------------------------------------------
# Aggregate stats for the right rail
# ---------------------------------------------------------------
def _week_summary(trainer):
    week_ago = timezone.now() - timedelta(days=7)
    return {
        "new_clients": User.objects.filter(
            role=User.CLIENT,
            client_profile__trainer=trainer.trainer_profile,
            date_joined__gte=week_ago,
        ).count(),
        "plans_created": WorkoutPlan.objects.filter(
            user=trainer, created_at__gte=week_ago,
        ).count(),
        "nutrition_plans": NutritionPlan.objects.filter(
            user=trainer, created_at__gte=week_ago,
        ).count(),
        "forms_built": CheckInForm.objects.filter(
            user=trainer, created_at__gte=week_ago,
        ).count(),
    }


def _next_actions(trainer):
    """Tiny suggestion engine — surfaces the most useful next action
    based on what's missing. Returns an ordered list of dicts."""
    actions = []

    # Clients with no workout plan
    missing_workout = User.objects.filter(
        role=User.CLIENT,
        client_profile__trainer=trainer.trainer_profile,
        client_profile__assigned_workout_plan__isnull=True,
    ).count()
    if missing_workout:
        actions.append({
            "title": f"{missing_workout} client{'s' if missing_workout != 1 else ''} need{'s' if missing_workout == 1 else ''} a workout plan",
            "url": reverse("trainer-dashboard"),
            "cta": "Open Clients",
        })

    # No check-in forms yet
    if not CheckInForm.objects.filter(user=trainer).exists():
        actions.append({
            "title": "You haven't built a check-in form yet",
            "url": reverse("trainer-checkin-forms-page"),
            "cta": "Build one",
        })

    # No workout plans yet
    if not WorkoutPlan.objects.filter(user=trainer).exists():
        actions.append({
            "title": "Create your first workout plan",
            "url": reverse("trainer-dashboard-home"),
            "cta": "Open Workouts",
        })

    return actions[:3]


@login_required
def trainer_activity_page(request):
    if not trainer_required(request):
        return redirect("landing-page")

    kind = request.GET.get("kind", "all")
    if kind not in {slug for slug, _ in EVENT_KIND_CHOICES}:
        kind = "all"

    days_param = request.GET.get("days", "30")
    since = _since(days_param)

    client_filter_id = request.GET.get("client") or ""
    try:
        client_filter_id = int(client_filter_id)
    except (TypeError, ValueError):
        client_filter_id = 0

    events = _collect_events(request.user, kind, since, client_filter_id)
    grouped = _group_by_bucket(events)

    clients = get_trainer_clients(request)

    # Build query-string fragments so each filter pill keeps the other
    # filter values the user already picked.
    def make_qs(**override):
        params = {"kind": kind, "days": days_param}
        if client_filter_id:
            params["client"] = client_filter_id
        params.update(override)
        return urlencode({k: v for k, v in params.items() if v not in (None, "", 0, "0")})

    kind_pills = []
    for slug, label in EVENT_KIND_CHOICES:
        kind_pills.append({
            "slug": slug,
            "label": label,
            "active": slug == kind,
            "qs": make_qs(kind=slug),
        })

    context = dashboard_context(request, "Activity")
    context.update({
        "events": events,
        "grouped_events": grouped,
        "kind_pills": kind_pills,
        "active_kind": kind,
        "active_days": days_param,
        "active_client_id": client_filter_id,
        "time_range_choices": TIME_RANGE_CHOICES,
        "filter_clients": clients,
        "week_summary": _week_summary(request.user),
        "next_actions": _next_actions(request.user),
        "make_qs": make_qs,
    })
    return render(request, "dashboard/dashboard_activity.html", context)
