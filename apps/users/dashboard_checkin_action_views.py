from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect

from .dashboard_helpers import trainer_required
from .forms import (
    CreateCheckInFormForm,
    UpdateCheckInFormForm,
    CreateCheckInQuestionForm,
    UpdateCheckInQuestionForm,
)
from apps.progress.models import CheckInForm, CheckInQuestion, CheckInQuestionOption


def _replace_dropdown_options(question, raw_options_text):
    question.options.all().delete()

    options = [
        line.strip()
        for line in raw_options_text.splitlines()
        if line.strip()
    ]

    for index, value in enumerate(options, start=1):
        CheckInQuestionOption.objects.create(
            question=question,
            value=value,
            order=index,
        )


def _create_question(form, order, question_text, question_type, field_key, *, is_required=True, is_system_question=True, dropdown_options=None):
    question = CheckInQuestion.objects.create(
        form=form,
        question_text=question_text,
        question_type=question_type,
        is_required=is_required,
        order=order,
        field_key=field_key,
        is_system_question=is_system_question,
    )

    if dropdown_options:
        for index, value in enumerate(dropdown_options, start=1):
            CheckInQuestionOption.objects.create(
                question=question,
                value=value,
                order=index,
            )

    return question


def _create_default_onboarding_questions(checkin_form):
    _create_question(checkin_form, 1, "Full name", CheckInQuestion.SHORT_TEXT, "full_name")
    _create_question(checkin_form, 2, "Email", CheckInQuestion.SHORT_TEXT, "email")
    _create_question(checkin_form, 3, "Age", CheckInQuestion.NUMBER, "age")
    _create_question(checkin_form, 4, "Height (cm)", CheckInQuestion.NUMBER, "height_cm")
    _create_question(checkin_form, 5, "Current weight (kg)", CheckInQuestion.NUMBER, "current_weight")
    _create_question(
        checkin_form,
        6,
        "Main goal",
        CheckInQuestion.DROPDOWN,
        "main_goal",
        dropdown_options=[
            "Fat loss",
            "Muscle gain",
            "Recomp",
            "General fitness",
            "Strength",
            "Endurance",
        ],
    )
    _create_question(checkin_form, 7, "Goal weight (kg)", CheckInQuestion.NUMBER, "goal_weight")
    _create_question(checkin_form, 8, "Goal deadline", CheckInQuestion.SHORT_TEXT, "goal_deadline")
    _create_question(
        checkin_form,
        9,
        "Training experience level",
        CheckInQuestion.DROPDOWN,
        "training_experience",
        dropdown_options=[
            "Beginner",
            "Intermediate",
            "Advanced",
        ],
    )
    _create_question(checkin_form, 10, "How many days per week can you train?", CheckInQuestion.NUMBER, "training_days_per_week")
    _create_question(
        checkin_form,
        11,
        "Do you train at a gym or at home?",
        CheckInQuestion.DROPDOWN,
        "training_location",
        dropdown_options=[
            "Gym",
            "Home",
            "Both",
        ],
    )
    _create_question(checkin_form, 12, "Injuries or limitations", CheckInQuestion.LONG_TEXT, "injuries_limitations")
    _create_question(checkin_form, 13, "Dietary preferences or restrictions", CheckInQuestion.LONG_TEXT, "dietary_preferences")


def _create_default_daily_questions(checkin_form):
    _create_question(checkin_form, 1, "Current weight (kg)", CheckInQuestion.NUMBER, "daily_weight")
    _create_question(checkin_form, 2, "Steps today", CheckInQuestion.NUMBER, "daily_steps")
    _create_question(checkin_form, 3, "Water intake (litres)", CheckInQuestion.NUMBER, "daily_water")
    _create_question(checkin_form, 4, "Sleep last night (hours)", CheckInQuestion.NUMBER, "daily_sleep_hours")
    _create_question(
        checkin_form,
        5,
        "Energy today",
        CheckInQuestion.DROPDOWN,
        "daily_energy",
        dropdown_options=[
            "Very low",
            "Low",
            "Okay",
            "Good",
            "Very good",
        ],
    )
    _create_question(checkin_form, 6, "Did you follow your nutrition plan today?", CheckInQuestion.YES_NO, "daily_nutrition_adherence")
    _create_question(checkin_form, 7, "Did you complete your workout today?", CheckInQuestion.YES_NO, "daily_workout_completed")


def _create_default_weekly_questions(checkin_form):
    _create_question(checkin_form, 1, "Current weight (kg)", CheckInQuestion.NUMBER, "weekly_weight")
    _create_question(
        checkin_form,
        2,
        "How closely did you follow your nutrition plan this week?",
        CheckInQuestion.DROPDOWN,
        "weekly_nutrition_adherence",
        dropdown_options=[
            "Very poor",
            "Poor",
            "Average",
            "Good",
            "Perfect",
        ],
    )
    _create_question(
        checkin_form,
        3,
        "How closely did you follow your workout plan this week?",
        CheckInQuestion.DROPDOWN,
        "weekly_workout_adherence",
        dropdown_options=[
            "Very poor",
            "Poor",
            "Average",
            "Good",
            "Perfect",
        ],
    )
    _create_question(
        checkin_form,
        4,
        "How was your energy this week?",
        CheckInQuestion.DROPDOWN,
        "weekly_energy",
        dropdown_options=[
            "Very low",
            "Low",
            "Okay",
            "Good",
            "Very good",
        ],
    )
    _create_question(
        checkin_form,
        5,
        "How was your sleep this week?",
        CheckInQuestion.DROPDOWN,
        "weekly_sleep_quality",
        dropdown_options=[
            "Very poor",
            "Poor",
            "Average",
            "Good",
            "Very good",
        ],
    )
    _create_question(
        checkin_form,
        6,
        "How was your digestion this week?",
        CheckInQuestion.DROPDOWN,
        "weekly_digestion",
        dropdown_options=[
            "Very poor",
            "Poor",
            "Average",
            "Good",
            "Very good",
        ],
    )
    _create_question(checkin_form, 7, "Progress photos", CheckInQuestion.PHOTO, "weekly_progress_photos")
    _create_question(checkin_form, 8, "What went well this week?", CheckInQuestion.LONG_TEXT, "weekly_wins")
    _create_question(checkin_form, 9, "What did you struggle with this week?", CheckInQuestion.LONG_TEXT, "weekly_struggles")


def _create_default_questions_for_form(checkin_form):
    if checkin_form.form_type == CheckInForm.ONBOARDING:
        _create_default_onboarding_questions(checkin_form)
    elif checkin_form.form_type == CheckInForm.DAILY:
        _create_default_daily_questions(checkin_form)
    elif checkin_form.form_type == CheckInForm.WEEKLY:
        _create_default_weekly_questions(checkin_form)


@login_required
def dashboard_create_checkin_form(request):
    if not trainer_required(request):
        return redirect("landing-page")

    if request.method != "POST":
        return redirect("trainer-checkin-forms-page")

    form = CreateCheckInFormForm(request.POST)

    if not form.is_valid():
        for _, errors in form.errors.items():
            for error in errors:
                messages.error(request, error)
        return redirect("trainer-checkin-forms-page")

    checkin_form = CheckInForm.objects.create(
        user=request.user,
        name=form.cleaned_data["name"],
        form_type=form.cleaned_data["form_type"],
        is_active=True,
    )

    _create_default_questions_for_form(checkin_form)

    messages.success(request, "Check-in form created successfully.")
    return redirect("trainer-checkin-form-detail", form_id=checkin_form.id)


@login_required
def dashboard_update_checkin_form(request, form_id):
    if not trainer_required(request):
        return redirect("landing-page")

    checkin_form = get_object_or_404(CheckInForm, id=form_id, user=request.user)

    if request.method != "POST":
        return redirect("trainer-checkin-form-detail", form_id=checkin_form.id)

    form = UpdateCheckInFormForm(request.POST)

    if not form.is_valid():
        for _, errors in form.errors.items():
            for error in errors:
                messages.error(request, error)
        return redirect("trainer-checkin-form-detail", form_id=checkin_form.id)

    original_form_type = checkin_form.form_type
    new_form_type = form.cleaned_data["form_type"]

    if original_form_type != new_form_type and checkin_form.questions.filter(is_system_question=True).exists():
        messages.error(request, "You cannot change the type of a form after system questions have been created.")
        return redirect("trainer-checkin-form-detail", form_id=checkin_form.id)

    checkin_form.name = form.cleaned_data["name"]
    checkin_form.form_type = new_form_type
    checkin_form.is_active = form.cleaned_data["is_active"]
    checkin_form.save()

    messages.success(request, "Check-in form updated successfully.")
    return redirect("trainer-checkin-form-detail", form_id=checkin_form.id)


@login_required
def dashboard_delete_checkin_form(request, form_id):
    if not trainer_required(request):
        return redirect("landing-page")

    checkin_form = get_object_or_404(CheckInForm, id=form_id, user=request.user)

    if request.method != "POST":
        return redirect("trainer-checkin-form-detail", form_id=checkin_form.id)

    form_name = checkin_form.name
    checkin_form.delete()

    messages.success(request, f'Check-in form "{form_name}" deleted successfully.')
    return redirect("trainer-checkin-forms-page")


@login_required
def dashboard_create_checkin_question(request, form_id):
    if not trainer_required(request):
        return redirect("landing-page")

    checkin_form = get_object_or_404(CheckInForm, id=form_id, user=request.user)

    if request.method != "POST":
        return redirect("trainer-checkin-form-detail", form_id=checkin_form.id)

    form = CreateCheckInQuestionForm(request.POST)

    if not form.is_valid():
        for _, errors in form.errors.items():
            for error in errors:
                messages.error(request, error)
        return redirect("trainer-checkin-form-detail", form_id=checkin_form.id)

    question = CheckInQuestion.objects.create(
        form=checkin_form,
        question_text=form.cleaned_data["question_text"],
        question_type=form.cleaned_data["question_type"],
        is_required=form.cleaned_data["is_required"],
        order=form.cleaned_data["order"],
        field_key="",
        is_system_question=False,
    )

    if question.question_type == CheckInQuestion.DROPDOWN:
        _replace_dropdown_options(question, form.cleaned_data["dropdown_options"])

    messages.success(request, "Question added successfully.")
    return redirect("trainer-checkin-form-detail", form_id=checkin_form.id)


@login_required
def dashboard_update_checkin_question(request, form_id, question_id):
    if not trainer_required(request):
        return redirect("landing-page")

    checkin_form = get_object_or_404(CheckInForm, id=form_id, user=request.user)
    question = get_object_or_404(CheckInQuestion, id=question_id, form=checkin_form)

    if request.method != "POST":
        return redirect("trainer-checkin-form-detail", form_id=checkin_form.id)

    form = UpdateCheckInQuestionForm(request.POST)

    if not form.is_valid():
        for _, errors in form.errors.items():
            for error in errors:
                messages.error(request, error)
        return redirect("trainer-checkin-form-detail", form_id=checkin_form.id)

    question.question_text = form.cleaned_data["question_text"]
    question.order = form.cleaned_data["order"]

    if question.is_system_question:
        question.is_required = True
    else:
        question.question_type = form.cleaned_data["question_type"]
        question.is_required = form.cleaned_data["is_required"]

    question.save()

    if question.question_type == CheckInQuestion.DROPDOWN:
        _replace_dropdown_options(question, form.cleaned_data["dropdown_options"])
    else:
        question.options.all().delete()

    messages.success(request, "Question updated successfully.")
    return redirect("trainer-checkin-form-detail", form_id=checkin_form.id)


@login_required
def dashboard_delete_checkin_question(request, form_id, question_id):
    if not trainer_required(request):
        return redirect("landing-page")

    checkin_form = get_object_or_404(CheckInForm, id=form_id, user=request.user)
    question = get_object_or_404(CheckInQuestion, id=question_id, form=checkin_form)

    if request.method != "POST":
        return redirect("trainer-checkin-form-detail", form_id=checkin_form.id)

    if question.is_system_question:
        messages.error(request, "This is a required system question and cannot be deleted.")
        return redirect("trainer-checkin-form-detail", form_id=checkin_form.id)

    question_text = question.question_text
    question.delete()

    messages.success(request, f'Question "{question_text}" deleted successfully.')
    return redirect("trainer-checkin-form-detail", form_id=checkin_form.id)
