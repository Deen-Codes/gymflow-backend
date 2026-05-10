# T2.9 — MealTemplate + MealTemplateItem. User-saved meals from the
# food catalog, reusable as one-tap log entries to the daily diary.
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nutrition", "0012_nutritiontemplate"),
    ]

    operations = [
        migrations.CreateModel(
            name="MealTemplate",
            fields=[
                ("id", models.AutoField(
                    auto_created=True, primary_key=True, serialize=False,
                    verbose_name="ID",
                )),
                ("title", models.CharField(max_length=120)),
                ("slot", models.CharField(
                    choices=[
                        ("breakfast",     "Breakfast"),
                        ("lunch",         "Lunch"),
                        ("dinner",        "Dinner"),
                        ("snack",         "Snack"),
                        ("pre_workout",   "Pre-workout"),
                        ("intra_workout", "Intra-workout"),
                        ("post_workout",  "Post-workout"),
                    ],
                    default="breakfast",
                    db_index=True,
                    max_length=20,
                )),
                ("notes", models.CharField(blank=True, max_length=240)),
                ("source", models.CharField(
                    choices=[
                        ("user_edit",    "User-built"),
                        ("ai_generated", "AI-generated"),
                    ],
                    default="user_edit",
                    max_length=16,
                )),
                ("is_favourite", models.BooleanField(default=True)),
                ("created_at",   models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at",   models.DateTimeField(auto_now=True)),
                ("user", models.ForeignKey(
                    to=settings.AUTH_USER_MODEL,
                    on_delete=models.CASCADE,
                    related_name="meal_templates",
                )),
            ],
            options={
                "ordering": ["-is_favourite", "-updated_at"],
                "indexes": [
                    models.Index(fields=["user", "slot"], name="nutrition_m_user_id_d70a14_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="MealTemplateItem",
            fields=[
                ("id", models.AutoField(
                    auto_created=True, primary_key=True, serialize=False,
                    verbose_name="ID",
                )),
                ("portion_g", models.FloatField()),
                ("order",     models.PositiveSmallIntegerField(default=0)),
                ("food", models.ForeignKey(
                    to="nutrition.curatedfood",
                    on_delete=models.PROTECT,
                    related_name="meal_template_items",
                )),
                ("template", models.ForeignKey(
                    to="nutrition.mealtemplate",
                    on_delete=models.CASCADE,
                    related_name="items",
                )),
            ],
            options={
                "ordering": ["order", "id"],
            },
        ),
    ]
