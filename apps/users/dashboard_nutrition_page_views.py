from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .dashboard_helpers import trainer_required, dashboard_context
from .forms import UpdateNutritionPlanForm
from apps.nutrition.models import NutritionPlan


@login_required
def trainer_nutrition_plans_page(request):
    """
    Trainer nutrition plans page.
    """
    if not trainer_required(request):
        return redirect("landing-page")

    context = dashboard_context(request, "Nutrition Plans")
    return render(request, "dashboard/dashboard_nutrition_plans.html", context)


@login_required
def trainer_nutrition_plan_detail_page(request, plan_id):
    """
    Detail page for a single trainer-owned nutrition plan.
    """
    if not trainer_required(request):
        return redirect("landing-page")

    plan = get_object_or_404(
        NutritionPlan,
        id=plan_id,
        user=request.user,
    )

    context = dashboard_context(request, f"Nutrition: {plan.name}")
    context.update({
        "nutrition_plan": plan,
        "nutrition_plan_edit_form": UpdateNutritionPlanForm(
            initial={
                "name": plan.name,
                "calories_target": plan.calories_target,
                "protein_target": plan.protein_target,
                "carbs_target": plan.carbs_target,
                "fats_target": plan.fats_target,
                "notes": plan.notes,
            }
        ),
    })
    return render(request, "dashboard/nutrition_plan_detail.html", context)
