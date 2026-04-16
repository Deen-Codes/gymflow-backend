from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .dashboard_helpers import trainer_required, dashboard_context
from .forms import (
    CreateCheckInFormForm,
    UpdateCheckInFormForm,
    CreateCheckInQuestionForm,
    UpdateCheckInQuestionForm,
)
from apps.progress.models import CheckInForm


@login_required
def trainer_checkin_forms_page(request):
    """
    Trainer check-in forms list page.
    """
    if not trainer_required(request):
        return redirect("landing-page")

    checkin_forms = CheckInForm.objects.filter(user=request.user).order_by("form_type", "name")

    context = dashboard_context(request, "Check-In Forms")
    context.update({
        "checkin_forms": checkin_forms,
        "create_checkin_form_form": CreateCheckInFormForm(),
    })
    return render(request, "dashboard/dashboard_checkin_forms.html", context)


@login_required
def trainer_checkin_form_detail_page(request, form_id):
    """
    Trainer check-in form builder page.
    """
    if not trainer_required(request):
        return redirect("landing-page")

    checkin_form = get_object_or_404(
        CheckInForm.objects.prefetch_related("questions__options"),
        id=form_id,
        user=request.user,
    )

    question_edit_forms = {
        question.id: UpdateCheckInQuestionForm(
            initial={
                "question_text": question.question_text,
                "question_type": question.question_type,
                "is_required": question.is_required,
                "order": question.order,
                "dropdown_options": "\n".join(
                    option.value for option in question.options.all().order_by("order")
                ),
            }
        )
        for question in checkin_form.questions.all()
    }

    context = dashboard_context(request, f"Check-In Form: {checkin_form.name}")
    context.update({
        "checkin_form": checkin_form,
        "checkin_form_edit_form": UpdateCheckInFormForm(
            initial={
                "name": checkin_form.name,
                "form_type": checkin_form.form_type,
                "is_active": checkin_form.is_active,
            }
        ),
        "create_question_form": CreateCheckInQuestionForm(),
        "question_edit_forms": question_edit_forms,
    })
    return render(request, "dashboard/checkin_form_detail.html", context)
