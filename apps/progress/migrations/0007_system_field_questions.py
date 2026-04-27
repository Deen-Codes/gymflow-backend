"""Add system_field_key to CheckInQuestion + DATE question type +
value_date column on CheckInAnswer.

Plus a data migration that retro-fits every existing onboarding form
with a "Date of birth" question (DATE type, system_field_key=
"date_of_birth"). New trainers get this question via the bootstrap
seeder; existing trainers get it via this data step so their clients
hit the same flow.

We deliberately do NOT remove the legacy "Age" question — old
submissions reference it and we don't want to orphan answers.
Trainers can delete it manually from their dashboard if they want.
"""
from django.db import migrations, models


def add_dob_question_to_existing_onboarding_forms(apps, schema_editor):
    CheckInForm = apps.get_model("progress", "CheckInForm")
    CheckInQuestion = apps.get_model("progress", "CheckInQuestion")

    for form in CheckInForm.objects.filter(form_type="onboarding"):
        # Skip if a DOB question is already attached (idempotency for
        # restored backups + for trainers who manually added one
        # before this migration).
        if form.questions.filter(system_field_key="date_of_birth").exists():
            continue
        # Slot just after the email question if we can find it (so
        # name/email/dob ordering reads naturally), otherwise at the
        # top.
        existing_orders = list(
            form.questions.values_list("order", flat=True).order_by("order")
        )
        target_order = 3   # default position right where "Age" used to live
        # Bump everything at >= target_order down by 1 to make room.
        for q in form.questions.filter(order__gte=target_order).order_by("-order"):
            q.order += 1
            q.save(update_fields=["order"])
        CheckInQuestion.objects.create(
            form=form,
            question_text="Date of birth",
            question_type="date",
            is_required=True,
            order=target_order,
            field_key="date_of_birth",
            is_system_question=True,
            system_field_key="date_of_birth",
        )


def remove_dob_question_from_onboarding_forms(apps, schema_editor):
    CheckInQuestion = apps.get_model("progress", "CheckInQuestion")
    CheckInQuestion.objects.filter(system_field_key="date_of_birth").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("progress", "0006_hydration_log"),
    ]

    operations = [
        migrations.AddField(
            model_name="checkinquestion",
            name="system_field_key",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
        migrations.AlterField(
            model_name="checkinquestion",
            name="question_type",
            field=models.CharField(
                choices=[
                    ("short_text", "Short Text"),
                    ("long_text",  "Long Text"),
                    ("number",     "Number"),
                    ("yes_no",     "Yes / No"),
                    ("dropdown",   "Dropdown"),
                    ("photo",      "Photo Upload"),
                    ("video",      "Video Upload"),
                    ("date",       "Date"),
                ],
                max_length=50,
            ),
        ),
        migrations.AddField(
            model_name="checkinanswer",
            name="value_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.RunPython(
            add_dob_question_to_existing_onboarding_forms,
            reverse_code=remove_dob_question_from_onboarding_forms,
        ),
    ]
