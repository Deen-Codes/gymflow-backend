from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect

from .dashboard_helpers import trainer_required
from .forms import (
    CreateNutritionPlanForm,
    UpdateNutritionPlanForm,
    CreateFoodLibraryItemForm,
    UpdateFoodLibraryItemForm,
    CreateNutritionMealForm,
    UpdateNutritionMealForm,
    AddFoodToNutritionMealForm,
)
from .models import User
from apps.nutrition.models import (
    NutritionPlan,
    FoodLibraryItem,
    NutritionMeal,
    NutritionMealItem,
)


@login_required
def dashboard_create_nutrition_plan(request):
    if not trainer_required(request):
        return redirect("landing-page")

    if request.method != "POST":
        return redirect("trainer-nutrition-plans-page")

    form = CreateNutritionPlanForm(request.POST)

    if not form.is_valid():
        for _, errors in form.errors.items():
            for error in errors:
                messages.error(request, error)
        return redirect("trainer-nutrition-plans-page")

    plan = NutritionPlan.objects.create(
        user=request.user,
        name=form.cleaned_data["name"],
        calories_target=form.cleaned_data["calories_target"],
        protein_target=form.cleaned_data["protein_target"],
        carbs_target=form.cleaned_data["carbs_target"],
        fats_target=form.cleaned_data["fats_target"],
        notes=form.cleaned_data["notes"],
        is_active=True,
        is_template=True,
    )

    messages.success(request, "Nutrition plan created successfully.")
    return redirect("trainer-nutrition-plan-detail", plan_id=plan.id)


@login_required
def dashboard_update_nutrition_plan(request, plan_id):
    if not trainer_required(request):
        return redirect("landing-page")

    plan = get_object_or_404(NutritionPlan, id=plan_id, user=request.user)

    if request.method != "POST":
        return redirect("trainer-nutrition-plan-detail", plan_id=plan.id)

    form = UpdateNutritionPlanForm(request.POST)

    if not form.is_valid():
        for _, errors in form.errors.items():
            for error in errors:
                messages.error(request, error)
        return redirect("trainer-nutrition-plan-detail", plan_id=plan.id)

    plan.name = form.cleaned_data["name"]
    plan.calories_target = form.cleaned_data["calories_target"]
    plan.protein_target = form.cleaned_data["protein_target"]
    plan.carbs_target = form.cleaned_data["carbs_target"]
    plan.fats_target = form.cleaned_data["fats_target"]
    plan.notes = form.cleaned_data["notes"]
    plan.save()

    messages.success(request, "Nutrition plan updated successfully.")
    return redirect("trainer-nutrition-plan-detail", plan_id=plan.id)


@login_required
def dashboard_delete_nutrition_plan(request, plan_id):
    if not trainer_required(request):
        return redirect("landing-page")

    plan = get_object_or_404(NutritionPlan, id=plan_id, user=request.user)

    if request.method != "POST":
        return redirect("trainer-nutrition-plan-detail", plan_id=plan.id)

    plan_name = plan.name
    plan.delete()

    messages.success(request, f'Nutrition plan "{plan_name}" deleted successfully.')
    return redirect("trainer-nutrition-plans-page")


@login_required
def dashboard_duplicate_nutrition_plan(request, plan_id):
    if not trainer_required(request):
        return redirect("landing-page")

    source_plan = get_object_or_404(
        NutritionPlan.objects.prefetch_related("meals__items"),
        id=plan_id,
        user=request.user,
    )

    if request.method != "POST":
        return redirect("trainer-nutrition-plan-detail", plan_id=source_plan.id)

    with transaction.atomic():
        new_plan = NutritionPlan.objects.create(
            user=request.user,
            name=f"{source_plan.name} Copy",
            calories_target=source_plan.calories_target,
            protein_target=source_plan.protein_target,
            carbs_target=source_plan.carbs_target,
            fats_target=source_plan.fats_target,
            notes=source_plan.notes,
            is_active=source_plan.is_active,
            is_template=True,
        )

        for meal in source_plan.meals.all().order_by("order"):
            new_meal = NutritionMeal.objects.create(
                nutrition_plan=new_plan,
                title=meal.title,
                order=meal.order,
            )

            for item in meal.items.all().order_by("order"):
                NutritionMealItem.objects.create(
                    meal=new_meal,
                    food_library_item=item.food_library_item,
                    food_name=item.food_name,
                    reference_grams=item.reference_grams,
                    grams=item.grams,
                    calories=item.calories,
                    protein=item.protein,
                    carbs=item.carbs,
                    fats=item.fats,
                    order=item.order,
                )

    messages.success(request, f'Nutrition plan "{source_plan.name}" duplicated successfully.')
    return redirect("trainer-nutrition-plan-detail", plan_id=new_plan.id)


@login_required
def dashboard_assign_nutrition_plan(request):
    if not trainer_required(request):
        return redirect("landing-page")

    if request.method != "POST":
        return redirect("trainer-dashboard")

    from .forms import AssignNutritionPlanForm

    form = AssignNutritionPlanForm(request.POST, trainer_user=request.user)

    if not form.is_valid():
        for _, errors in form.errors.items():
            for error in errors:
                messages.error(request, error)
        return redirect("trainer-dashboard")

    client_user = get_object_or_404(
        User.objects.select_related("client_profile"),
        id=form.cleaned_data["client_user_id"],
        role=User.CLIENT,
        client_profile__trainer=request.user.trainer_profile,
    )

    selected_plan = get_object_or_404(
        NutritionPlan.objects.prefetch_related("meals__items"),
        id=form.cleaned_data["nutrition_plan_id"],
        user=request.user,
        is_template=True,
    )

    create_client_specific_copy = form.cleaned_data["create_client_specific_copy"]

    if create_client_specific_copy:
        with transaction.atomic():
            copied_plan = NutritionPlan.objects.create(
                user=request.user,
                name=f"{selected_plan.name} - {client_user.username}",
                calories_target=selected_plan.calories_target,
                protein_target=selected_plan.protein_target,
                carbs_target=selected_plan.carbs_target,
                fats_target=selected_plan.fats_target,
                notes=selected_plan.notes,
                is_active=selected_plan.is_active,
                is_template=False,
                source_template=selected_plan,
                client=client_user,
            )

            for meal in selected_plan.meals.all().order_by("order"):
                copied_meal = NutritionMeal.objects.create(
                    nutrition_plan=copied_plan,
                    title=meal.title,
                    order=meal.order,
                )

                for item in meal.items.all().order_by("order"):
                    NutritionMealItem.objects.create(
                        meal=copied_meal,
                        food_library_item=item.food_library_item,
                        food_name=item.food_name,
                        reference_grams=item.reference_grams,
                        grams=item.grams,
                        calories=item.calories,
                        protein=item.protein,
                        carbs=item.carbs,
                        fats=item.fats,
                        order=item.order,
                    )

            client_user.client_profile.assigned_nutrition_plan = copied_plan
            client_user.client_profile.save()

        messages.success(
            request,
            f'Created a client-specific version of "{selected_plan.name}" for {client_user.username}.',
        )
    else:
        client_user.client_profile.assigned_nutrition_plan = selected_plan
        client_user.client_profile.save()

        messages.success(request, "Nutrition plan assigned successfully.")

    return redirect("trainer-client-detail", client_id=client_user.id)


@login_required
def dashboard_create_food_library_item(request):
    if not trainer_required(request):
        return redirect("landing-page")

    if request.method != "POST":
        return redirect("trainer-nutrition-plans-page")

    form = CreateFoodLibraryItemForm(request.POST)

    if not form.is_valid():
        for _, errors in form.errors.items():
            for error in errors:
                messages.error(request, error)
        return redirect("trainer-nutrition-plans-page")

    FoodLibraryItem.objects.create(
        user=request.user,
        name=form.cleaned_data["name"],
        portion_type=form.cleaned_data.get("portion_type") or FoodLibraryItem.PORTION_GRAMS,
        unit_label=(form.cleaned_data.get("unit_label") or "").strip(),
        reference_grams=form.cleaned_data["reference_grams"],
        calories=form.cleaned_data["calories"],
        protein=form.cleaned_data["protein"],
        carbs=form.cleaned_data["carbs"],
        fats=form.cleaned_data["fats"],
    )

    messages.success(request, "Food preset created successfully.")
    return redirect("trainer-nutrition-plans-page")


@login_required
def dashboard_update_food_library_item(request, food_id):
    if not trainer_required(request):
        return redirect("landing-page")

    food = get_object_or_404(
        FoodLibraryItem,
        id=food_id,
        user=request.user,
    )

    if request.method != "POST":
        return redirect("trainer-nutrition-plans-page")

    form = UpdateFoodLibraryItemForm(request.POST)

    if not form.is_valid():
        for _, errors in form.errors.items():
            for error in errors:
                messages.error(request, error)
        return redirect("trainer-nutrition-plans-page")

    food.name = form.cleaned_data["name"]
    food.portion_type = form.cleaned_data.get("portion_type") or FoodLibraryItem.PORTION_GRAMS
    food.unit_label = (form.cleaned_data.get("unit_label") or "").strip()
    food.reference_grams = form.cleaned_data["reference_grams"]
    food.calories = form.cleaned_data["calories"]
    food.protein = form.cleaned_data["protein"]
    food.carbs = form.cleaned_data["carbs"]
    food.fats = form.cleaned_data["fats"]
    food.save()

    messages.success(request, f'Food preset "{food.name}" updated successfully.')
    return redirect("trainer-nutrition-plans-page")


@login_required
def dashboard_delete_food_library_item(request, food_id):
    if not trainer_required(request):
        return redirect("landing-page")

    food = get_object_or_404(
        FoodLibraryItem,
        id=food_id,
        user=request.user,
    )

    if request.method != "POST":
        return redirect("trainer-nutrition-plans-page")

    food_name = food.name
    food.delete()

    messages.success(request, f'Food preset "{food_name}" deleted successfully.')
    return redirect("trainer-nutrition-plans-page")


@login_required
def dashboard_duplicate_food_library_item(request, food_id):
    if not trainer_required(request):
        return redirect("landing-page")

    food = get_object_or_404(
        FoodLibraryItem,
        id=food_id,
        user=request.user,
    )

    if request.method != "POST":
        return redirect("trainer-nutrition-plans-page")

    FoodLibraryItem.objects.create(
        user=request.user,
        name=f"{food.name} Copy",
        reference_grams=food.reference_grams,
        calories=food.calories,
        protein=food.protein,
        carbs=food.carbs,
        fats=food.fats,
    )

    messages.success(request, f'Food preset "{food.name}" duplicated successfully.')
    return redirect("trainer-nutrition-plans-page")


@login_required
def dashboard_create_nutrition_meal(request, plan_id):
    if not trainer_required(request):
        return redirect("landing-page")

    plan = get_object_or_404(NutritionPlan, id=plan_id, user=request.user)

    if request.method != "POST":
        return redirect("trainer-nutrition-plan-detail", plan_id=plan.id)

    form = CreateNutritionMealForm(request.POST)

    if not form.is_valid():
        for _, errors in form.errors.items():
            for error in errors:
                messages.error(request, error)
        return redirect("trainer-nutrition-plan-detail", plan_id=plan.id)

    NutritionMeal.objects.create(
        nutrition_plan=plan,
        title=form.cleaned_data["title"],
        order=form.cleaned_data["order"],
    )

    messages.success(request, "Meal added successfully.")
    return redirect("trainer-nutrition-plan-detail", plan_id=plan.id)


@login_required
def dashboard_update_nutrition_meal(request, plan_id, meal_id):
    if not trainer_required(request):
        return redirect("landing-page")

    plan = get_object_or_404(NutritionPlan, id=plan_id, user=request.user)
    meal = get_object_or_404(
        NutritionMeal,
        id=meal_id,
        nutrition_plan=plan,
    )

    if request.method != "POST":
        return redirect("trainer-nutrition-plan-detail", plan_id=plan.id)

    form = UpdateNutritionMealForm(request.POST)

    if not form.is_valid():
        for _, errors in form.errors.items():
            for error in errors:
                messages.error(request, error)
        return redirect("trainer-nutrition-plan-detail", plan_id=plan.id)

    meal.title = form.cleaned_data["title"]
    meal.order = form.cleaned_data["order"]
    meal.save()

    messages.success(request, "Meal updated successfully.")
    return redirect("trainer-nutrition-plan-detail", plan_id=plan.id)


@login_required
def dashboard_delete_nutrition_meal(request, plan_id, meal_id):
    if not trainer_required(request):
        return redirect("landing-page")

    plan = get_object_or_404(NutritionPlan, id=plan_id, user=request.user)
    meal = get_object_or_404(
        NutritionMeal,
        id=meal_id,
        nutrition_plan=plan,
    )

    if request.method != "POST":
        return redirect("trainer-nutrition-plan-detail", plan_id=plan.id)

    meal_title = meal.title
    meal.delete()

    messages.success(request, f'Meal "{meal_title}" deleted successfully.')
    return redirect("trainer-nutrition-plan-detail", plan_id=plan.id)


@login_required
def dashboard_add_food_to_nutrition_meal(request, plan_id):
    if not trainer_required(request):
        return redirect("landing-page")

    plan = get_object_or_404(NutritionPlan, id=plan_id, user=request.user)

    if request.method != "POST":
        return redirect("trainer-nutrition-plan-detail", plan_id=plan.id)

    form = AddFoodToNutritionMealForm(request.POST, trainer_user=request.user)

    if not form.is_valid():
        for _, errors in form.errors.items():
            for error in errors:
                messages.error(request, error)
        return redirect("trainer-nutrition-plan-detail", plan_id=plan.id)

    meal = get_object_or_404(
        NutritionMeal,
        id=form.cleaned_data["meal_id"],
        nutrition_plan=plan,
    )

    food = get_object_or_404(
        FoodLibraryItem,
        id=form.cleaned_data["food_library_item_id"],
        user=request.user,
    )

    grams = form.cleaned_data["grams"]
    multiplier = grams / food.reference_grams

    NutritionMealItem.objects.create(
        meal=meal,
        food_library_item=food,
        food_name=food.name,
        reference_grams=food.reference_grams,
        grams=grams,
        calories=food.calories * multiplier,
        protein=food.protein * multiplier,
        carbs=food.carbs * multiplier,
        fats=food.fats * multiplier,
        order=form.cleaned_data["order"],
    )

    messages.success(request, "Food added to meal successfully.")
    return redirect("trainer-nutrition-plan-detail", plan_id=plan.id)


@login_required
def dashboard_delete_nutrition_meal_item(request, plan_id, item_id):
    if not trainer_required(request):
        return redirect("landing-page")

    plan = get_object_or_404(NutritionPlan, id=plan_id, user=request.user)
    item = get_object_or_404(
        NutritionMealItem.objects.select_related("meal", "meal__nutrition_plan"),
        id=item_id,
        meal__nutrition_plan=plan,
    )

    if request.method != "POST":
        return redirect("trainer-nutrition-plan-detail", plan_id=plan.id)

    item_name = item.food_name
    item.delete()

    messages.success(request, f'Food "{item_name}" removed from meal successfully.')
    return redirect("trainer-nutrition-plan-detail", plan_id=plan.id)
