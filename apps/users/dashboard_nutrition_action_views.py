from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect

from .dashboard_helpers import trainer_required
from .forms import CreateNutritionPlanForm, UpdateNutritionPlanForm
from apps.nutrition.models import NutritionPlan
from .models import User


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
        NutritionPlan,
        id=plan_id,
        user=request.user,
    )

    if request.method != "POST":
        return redirect("trainer-nutrition-plan-detail", plan_id=source_plan.id)

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
        NutritionPlan,
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
