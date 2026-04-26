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
from django.db.models import Sum
from django.views.decorators.csrf import csrf_exempt
from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.models import User


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
