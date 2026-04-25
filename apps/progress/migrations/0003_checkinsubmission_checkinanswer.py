from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("progress", "0002_checkinquestion_field_key_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CheckInSubmission",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(
                    choices=[("started", "Started"), ("submitted", "Submitted")],
                    default="started",
                    max_length=20,
                )),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("submitted_at", models.DateTimeField(blank=True, null=True)),
                ("client", models.ForeignKey(
                    on_delete=models.deletion.CASCADE,
                    related_name="checkin_submissions",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("form", models.ForeignKey(
                    on_delete=models.deletion.CASCADE,
                    related_name="submissions",
                    to="progress.checkinform",
                )),
            ],
            options={
                "ordering": ["-started_at"],
            },
        ),
        migrations.AddIndex(
            model_name="checkinsubmission",
            index=models.Index(
                fields=["form", "client", "-started_at"],
                name="progress_ch_form_id_client_idx",
            ),
        ),
        migrations.CreateModel(
            name="CheckInAnswer",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("value_text", models.TextField(blank=True, default="")),
                ("value_number", models.FloatField(blank=True, null=True)),
                ("value_image", models.ImageField(blank=True, null=True, upload_to="checkin_answers/photos/")),
                ("value_video", models.FileField(blank=True, null=True, upload_to="checkin_answers/videos/")),
                ("value_yes_no", models.BooleanField(blank=True, null=True)),
                ("answered_at", models.DateTimeField(auto_now=True)),
                ("question", models.ForeignKey(
                    on_delete=models.deletion.CASCADE,
                    related_name="answers",
                    to="progress.checkinquestion",
                )),
                ("submission", models.ForeignKey(
                    on_delete=models.deletion.CASCADE,
                    related_name="answers",
                    to="progress.checkinsubmission",
                )),
                ("value_option", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=models.deletion.SET_NULL,
                    related_name="selected_in_answers",
                    to="progress.checkinquestionoption",
                )),
            ],
            options={
                "ordering": ["question__order", "id"],
            },
        ),
        migrations.AddConstraint(
            model_name="checkinanswer",
            constraint=models.UniqueConstraint(
                fields=("submission", "question"),
                name="unique_answer_per_question_per_submission",
            ),
        ),
    ]
