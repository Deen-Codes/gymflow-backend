"""
Nutrition workspace page views.

Restructure v2: collapses the old "list of plans" page and the
"plan detail" page into a single Nutrition workspace. The same template
renders both routes:

    /dashboard/nutrition-plans/         → newest plan auto-selected
    /dashboard/nutrition-plans/<id>/    → that specific plan in the canvas
"""

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .dashboard_helpers import trainer_required, dashboard_context
from .forms import (
    UpdateNutritionPlanForm,
    CreateNutritionMealForm,
    UpdateNutritionMealForm,
)
from apps.nutrition.models import NutritionPlan


def _render_nutrition_workspace(request, plan_id=None):
    plans_qs = (
        NutritionPlan.objects
        .filter(user=request.user)
        .order_by("-id")
    )

    plan = None
    if plan_id is not None:
        plan = get_object_or_404(
            NutritionPlan.objects.prefetch_related("meals__items"),
            id=plan_id,
            user=request.user,
        )
    else:
        plan = plans_qs.prefetch_related("meals__items").first()

    meals = []
    meal_edit_forms = {}

    if plan is not None:
        meals = plan.meals.all().order_by("order")
        meal_edit_forms = {
            meal.id: UpdateNutritionMealForm(
                initial={"title": meal.title, "order": meal.order},
            )
            for meal in meals
        }

    page_title = f"Nutrition: {plan.name}" if plan else "Nutrition"
    context = dashboard_context(request, page_title)
    context.update({
        "nutrition_plan": plan,
        "meals": meals,
        "create_meal_form": CreateNutritionMealForm(),
        "meal_edit_forms": meal_edit_forms,
        "nutrition_plan_edit_form": UpdateNutritionPlanForm(
            initial={
                "name": plan.name,
                "calories_target": plan.calories_target,
                "protein_target": plan.protein_target,
                "carbs_target": plan.carbs_target,
                "fats_target": plan.fats_target,
                "notes": plan.notes,
            }
        ) if plan else None,
    })
    return render(request, "dashboard/dashboard_nutrition_plans.html", context)


@login_required
def trainer_nutrition_plans_page(request):
    """Front of the nutrition workspace — newest plan auto-selected."""
    if not trainer_required(request):
        return redirect("landing-page")
    return _render_nutrition_workspace(request, plan_id=None)


@login_required
def trainer_nutrition_plan_detail_page(request, plan_id):
    """Deep-link to a specific plan in the nutrition workspace."""
    if not trainer_required(request):
        return redirect("landing-page")
    return _render_nutrition_workspace(request, plan_id=plan_id)
