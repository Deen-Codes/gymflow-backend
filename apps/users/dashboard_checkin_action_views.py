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

    checkin_form.name = form.cleaned_data["name"]
    checkin_form.form_type = form.cleaned_data["form_type"]
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
    question.question_type = form.cleaned_data["question_type"]
    question.is_required = form.cleaned_data["is_required"]
    question.order = form.cleaned_data["order"]
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

    question_text = question.question_text
    question.delete()

    messages.success(request, f'Question "{question_text}" deleted successfully.')
    return redirect("trainer-checkin-form-detail", form_id=checkin_form.id)
