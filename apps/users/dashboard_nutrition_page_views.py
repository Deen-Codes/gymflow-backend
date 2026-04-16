from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .dashboard_helpers import trainer_required, dashboard_context
from .forms import (
    UpdateNutritionPlanForm,
    CreateFoodLibraryItemForm,
    UpdateFoodLibraryItemForm,
    CreateNutritionMealForm,
    UpdateNutritionMealForm,
    AddFoodToNutritionMealForm,
)
from apps.nutrition.models import NutritionPlan, FoodLibraryItem


@login_required
def trainer_nutrition_plans_page(request):
    """
    Trainer nutrition plans page.
    """
    if not trainer_required(request):
        return redirect("landing-page")

    context = dashboard_context(request, "Nutrition Plans")
    context.update({
        "food_library": FoodLibraryItem.objects.filter(user=request.user).order_by("name"),
        "create_food_library_item_form": CreateFoodLibraryItemForm(),
    })
    return render(request, "dashboard/dashboard_nutrition_plans.html", context)


@login_required
def trainer_nutrition_plan_detail_page(request, plan_id):
    """
    Detail page for a single trainer-owned nutrition plan.
    """
    if not trainer_required(request):
        return redirect("landing-page")

    plan = get_object_or_404(
        NutritionPlan.objects.prefetch_related("meals__items"),
        id=plan_id,
        user=request.user,
    )

    meals = plan.meals.all().order_by("order")
    meal_edit_forms = {
        meal.id: UpdateNutritionMealForm(
            initial={
                "title": meal.title,
                "order": meal.order,
            }
        )
        for meal in meals
    }
    add_food_forms = {
        meal.id: AddFoodToNutritionMealForm(
            trainer_user=request.user,
            initial={"meal_id": meal.id}
        )
        for meal in meals
    }

    context = dashboard_context(request, f"Nutrition: {plan.name}")
    context.update({
        "nutrition_plan": plan,
        "meals": meals,
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
        "create_meal_form": CreateNutritionMealForm(),
        "meal_edit_forms": meal_edit_forms,
        "add_food_forms": add_food_forms,
    })
    return render(request, "dashboard/nutrition_plan_detail.html", context)
