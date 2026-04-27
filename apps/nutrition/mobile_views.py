"""
Mobile API for the Nutrition feature on the iOS Home + Nutrition tabs.

Endpoint:
    GET /api/nutrition/me/today/
        Returns the client's currently assigned NutritionPlan with macro
        targets + the meals planned for today. "Today" is currently
        synonymous with "the assigned plan" since meals don't carry
        per-day overrides yet — the same plan applies every day until
        the trainer reassigns. Once we add per-day variation this
        endpoint stays the same shape; the data behind it gets richer.

Response shape (matches the iOS NutritionTodayResponse decoder):
{
  "status": "assigned" | "no_plan",
  "plan": {
    "id": 12,
    "name": "Lean bulk · 2400kcal",
    "calories_target": 2400,
    "protein_target": 180,
    "carbs_target":   240,
    "fats_target":    70,
    "meals": [
      {
        "id": 5,
        "title": "Breakfast",
        "calories": 480,
        "protein":  35,
        "carbs":    45,
        "fats":     12,
        "item_count": 4
      },
      ...
    ],
    "next_meal": { ... same shape as one meal ... } | null
  } | null
}
"""
from datetime import date as date_type, datetime

from django.db import IntegrityError
from django.db.models import Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.models import User
from .models import (
    NutritionMeal,
    NutritionMealItem,
    NutritionMealConsumption,
)


def _item_payload(item):
    """One food item inside a meal — what the user toggles when ticking."""
    return {
        "id":           item.id,
        "name":         item.food_name,
        "grams":        item.grams,
        "portion_type": item.portion_type,
        "unit_label":   item.unit_label,
        "units":        item.units,
        "calories":     round(item.calories or 0),
        "protein":      round(item.protein  or 0),
        "carbs":        round(item.carbs    or 0),
        "fats":         round(item.fats     or 0),
    }


def _meal_payload(meal):
    """Build the per-meal dict from a NutritionMeal + its items.

    `items` is included so the iOS meal-detail sheet can render
    the breakdown without a second API round-trip. The aggregated
    totals at the top level let the Home/Nutrition cards stay fast
    without iterating.
    """
    totals = meal.items.aggregate(
        cal=Sum("calories"),
        pro=Sum("protein"),
        car=Sum("carbs"),
        fat=Sum("fats"),
    )
    item_payloads = [_item_payload(i) for i in meal.items.all()]
    return {
        "id":         meal.id,
        "title":      meal.title,
        "calories":   round(totals["cal"] or 0),
        "protein":    round(totals["pro"] or 0),
        "carbs":      round(totals["car"] or 0),
        "fats":       round(totals["fat"] or 0),
        "item_count": len(item_payloads),
        "items":      item_payloads,
    }


@csrf_exempt
@api_view(["GET"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def nutrition_today_for_me(request):
    """Return today's nutrition plan + meals for the current client."""
    user = request.user
    if user.role != User.CLIENT or not hasattr(user, "client_profile"):
        return Response({"status": "no_plan", "plan": None})

    plan = user.client_profile.assigned_nutrition_plan
    if plan is None:
        return Response({"status": "no_plan", "plan": None})

    meals = list(plan.meals.all().prefetch_related("items"))
    meal_payloads = [_meal_payload(m) for m in meals]

    # Until per-meal timing lands, "next meal" is just the first
    # entry in the plan's meal order — the iOS card uses this to
    # show "Next: Breakfast" prominently. When we add a `time_of_day`
    # field on NutritionMeal this becomes server-truth.
    next_meal = meal_payloads[0] if meal_payloads else None

    return Response({
        "status": "assigned",
        "plan": {
            "id":              plan.id,
            "name":            plan.name,
            "calories_target": plan.calories_target,
            "protein_target":  plan.protein_target,
            "carbs_target":    plan.carbs_target,
            "fats_target":     plan.fats_target,
            "meals":           meal_payloads,
            "next_meal":       next_meal,
        },
    })


# ====================================================================
# Phase C.2 — Server-side meal consumption sync.
#
# Replaces iOS-local UserDefaults-only ticks with a server-of-record
# approach. iOS keeps an optimistic local cache for instant UI; this
# API is the persistent storage.
#
# Three endpoints:
#
#   GET  /api/nutrition/me/consumption/?date=YYYY-MM-DD
#        List ticks for the given date (defaults to today).
#        Used by iOS on app launch + tab focus to seed the cache.
#
#   POST /api/nutrition/me/consumption/
#        Body: {"meal_id": int, "item_id": int? , "date": "YYYY-MM-DD"?}
#        Idempotent: posting the same tick twice returns 200 with the
#        existing row, never errors. Lets iOS retry blindly without
#        tracking which ticks have already been synced.
#
#   DELETE /api/nutrition/me/consumption/?meal_id=X&item_id=Y&date=Z
#        Untick. Item-level if item_id is given, meal-level otherwise.
#        Returns 204 even if nothing was deleted (idempotent).
# ====================================================================


def _parse_date_param(raw):
    """Parse a YYYY-MM-DD string, falling back to today."""
    if not raw:
        return timezone.localdate()
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return timezone.localdate()


def _consumption_payload(row):
    """Compact JSON shape for one consumption row."""
    return {
        "id":          row.id,
        "meal_id":     row.meal_id,
        "item_id":     row.meal_item_id,    # null = whole-meal tick
        "consumed_on": row.consumed_on.isoformat(),
        "created_at":  row.created_at.isoformat(),
    }


@csrf_exempt
@api_view(["GET", "POST", "DELETE"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def consumption_for_me(request):
    """Multi-method endpoint covering list / tick / untick.

    Single URL keeps the iOS API surface tight — iOS only needs
    one URL constant and switches HTTP method based on the action.
    """
    user = request.user
    if user.role != User.CLIENT or not hasattr(user, "client_profile"):
        return Response({"detail": "Not a client."}, status=403)

    if request.method == "GET":
        return _list_consumption(user, request)
    if request.method == "POST":
        return _tick_consumption(user, request)
    return _untick_consumption(user, request)


def _list_consumption(user, request):
    target_date = _parse_date_param(request.query_params.get("date"))
    rows = (
        NutritionMealConsumption.objects
        .filter(client=user, consumed_on=target_date)
        .order_by("created_at")
    )
    return Response({
        "date":  target_date.isoformat(),
        "ticks": [_consumption_payload(r) for r in rows],
    })


def _tick_consumption(user, request):
    meal_id = request.data.get("meal_id")
    item_id = request.data.get("item_id")
    on_date = _parse_date_param(request.data.get("date"))

    if not meal_id:
        return Response({"detail": "meal_id is required."}, status=400)

    meal = get_object_or_404(NutritionMeal, id=meal_id)

    # Permission check: the meal must belong to a plan that this
    # client has assigned. Stops one client from logging ticks against
    # another trainer's meal plan.
    profile = user.client_profile
    if meal.nutrition_plan_id != getattr(profile.assigned_nutrition_plan, "id", None):
        return Response({"detail": "Meal not on your assigned plan."}, status=403)

    item = None
    if item_id:
        item = get_object_or_404(NutritionMealItem, id=item_id, meal=meal)

    # `update_or_create` would be wrong here because both the
    # constraint patterns (item_id null vs not-null) need different
    # filters. Use get_or_create + manual filter so unique-constraint
    # collisions return the existing row rather than 500.
    try:
        if item is not None:
            row, _created = NutritionMealConsumption.objects.get_or_create(
                client=user, meal=meal, meal_item=item, consumed_on=on_date,
            )
        else:
            row, _created = NutritionMealConsumption.objects.get_or_create(
                client=user, meal=meal, meal_item=None, consumed_on=on_date,
            )
    except IntegrityError:
        # Race-condition fallback — a duplicate write between SELECT
        # and INSERT. Read the existing row and return it.
        row = NutritionMealConsumption.objects.filter(
            client=user, meal=meal, meal_item=item, consumed_on=on_date,
        ).first()
        if row is None:
            return Response({"detail": "Couldn't record tick."}, status=500)

    return Response(_consumption_payload(row), status=201)


def _untick_consumption(user, request):
    meal_id = request.query_params.get("meal_id")
    item_id = request.query_params.get("item_id")
    on_date = _parse_date_param(request.query_params.get("date"))

    if not meal_id:
        return Response({"detail": "meal_id is required."}, status=400)

    qs = NutritionMealConsumption.objects.filter(
        client=user, meal_id=meal_id, consumed_on=on_date,
    )
    # When item_id is provided, narrow to that exact item-level row.
    # When it's not, target the meal-level row (meal_item is null).
    if item_id:
        qs = qs.filter(meal_item_id=item_id)
    else:
        qs = qs.filter(meal_item__isnull=True)

    qs.delete()    # idempotent — deleting nothing is fine
    return Response(status=204)
