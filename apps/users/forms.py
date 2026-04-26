from django import forms
from .models import User
from apps.workouts.models import WorkoutPlan, ExerciseLibraryItem
from apps.nutrition.models import NutritionPlan, FoodLibraryItem
from apps.progress.models import CheckInForm, CheckInQuestion


class TrainerLoginForm(forms.Form):
    username = forms.CharField(max_length=150)
    password = forms.CharField(widget=forms.PasswordInput)


class CreateClientForm(forms.Form):
    username = forms.CharField(max_length=150)
    email = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput, min_length=8)


class AssignWorkoutPlanForm(forms.Form):
    client_user_id = forms.IntegerField(widget=forms.HiddenInput)
    workout_plan_id = forms.ChoiceField(choices=[])
    create_client_specific_copy = forms.BooleanField(required=False)

    def __init__(self, *args, trainer_user=None, **kwargs):
        super().__init__(*args, **kwargs)

        if trainer_user is not None:
            plans = WorkoutPlan.objects.filter(
                user=trainer_user,
                is_template=True,
            ).order_by("name")

            self.fields["workout_plan_id"].choices = [
                (plan.id, plan.name) for plan in plans
            ]


class AssignNutritionPlanForm(forms.Form):
    client_user_id = forms.IntegerField(widget=forms.HiddenInput)
    nutrition_plan_id = forms.ChoiceField(choices=[])
    create_client_specific_copy = forms.BooleanField(required=False)

    def __init__(self, *args, trainer_user=None, **kwargs):
        super().__init__(*args, **kwargs)

        if trainer_user is not None:
            plans = NutritionPlan.objects.filter(
                user=trainer_user,
                is_template=True,
            ).order_by("name")

            self.fields["nutrition_plan_id"].choices = [
                (plan.id, plan.name) for plan in plans
            ]


class CreateExerciseLibraryItemForm(forms.Form):
    name = forms.CharField(max_length=255)
    video_url = forms.URLField(required=False)
    coaching_notes = forms.CharField(required=False, widget=forms.Textarea)


class CreateWorkoutPlanForm(forms.Form):
    name = forms.CharField(max_length=255)


class UpdateWorkoutPlanForm(forms.Form):
    name = forms.CharField(max_length=255)


class CreateWorkoutDayForm(forms.Form):
    title = forms.CharField(max_length=100)
    order = forms.IntegerField(min_value=1)


class UpdateWorkoutDayForm(forms.Form):
    title = forms.CharField(max_length=100)
    order = forms.IntegerField(min_value=1)


class AddExerciseToDayForm(forms.Form):
    workout_day_id = forms.IntegerField(widget=forms.HiddenInput)
    exercise_library_item_id = forms.ChoiceField(choices=[])
    label = forms.CharField(max_length=10)
    order = forms.IntegerField(min_value=1)
    superset_group = forms.IntegerField(required=False, min_value=1)
    set_count = forms.IntegerField(min_value=1, max_value=10)
    reps = forms.CharField(max_length=20)

    def __init__(self, *args, trainer_user=None, **kwargs):
        super().__init__(*args, **kwargs)

        if trainer_user is not None:
            exercises = ExerciseLibraryItem.objects.filter(user=trainer_user).order_by("name")
            self.fields["exercise_library_item_id"].choices = [
                (exercise.id, exercise.name) for exercise in exercises
            ]


class UpdateExerciseForm(forms.Form):
    label = forms.CharField(max_length=10)
    order = forms.IntegerField(min_value=1)
    superset_group = forms.IntegerField(required=False, min_value=1)
    set_count = forms.IntegerField(min_value=1, max_value=10)
    reps = forms.CharField(max_length=20)


class CreateNutritionPlanForm(forms.Form):
    name = forms.CharField(max_length=255)
    calories_target = forms.IntegerField(min_value=0)
    protein_target = forms.IntegerField(min_value=0)
    carbs_target = forms.IntegerField(min_value=0)
    fats_target = forms.IntegerField(min_value=0)
    notes = forms.CharField(required=False, widget=forms.Textarea)


class UpdateNutritionPlanForm(forms.Form):
    name = forms.CharField(max_length=255)
    calories_target = forms.IntegerField(min_value=0)
    protein_target = forms.IntegerField(min_value=0)
    carbs_target = forms.IntegerField(min_value=0)
    fats_target = forms.IntegerField(min_value=0)
    notes = forms.CharField(required=False, widget=forms.Textarea)


class CreateFoodLibraryItemForm(forms.Form):
    PORTION_CHOICES = [
        ("grams", "Per gram (weighed)"),
        ("unit",  "Per unit (eggs, wraps, scoops)"),
    ]

    name = forms.CharField(max_length=255)
    portion_type = forms.ChoiceField(choices=PORTION_CHOICES, initial="grams")
    unit_label = forms.CharField(max_length=40, required=False)
    reference_grams = forms.FloatField(min_value=0.01, initial=100)
    calories = forms.FloatField(min_value=0)
    protein = forms.FloatField(min_value=0)
    carbs = forms.FloatField(min_value=0)
    fats = forms.FloatField(min_value=0)


class UpdateFoodLibraryItemForm(forms.Form):
    PORTION_CHOICES = CreateFoodLibraryItemForm.PORTION_CHOICES

    name = forms.CharField(max_length=255)
    portion_type = forms.ChoiceField(choices=PORTION_CHOICES, initial="grams")
    unit_label = forms.CharField(max_length=40, required=False)
    reference_grams = forms.FloatField(min_value=0.01)
    calories = forms.FloatField(min_value=0)
    protein = forms.FloatField(min_value=0)
    carbs = forms.FloatField(min_value=0)
    fats = forms.FloatField(min_value=0)


class CreateNutritionMealForm(forms.Form):
    title = forms.CharField(max_length=100)
    order = forms.IntegerField(min_value=1)


class UpdateNutritionMealForm(forms.Form):
    title = forms.CharField(max_length=100)
    order = forms.IntegerField(min_value=1)


class AddFoodToNutritionMealForm(forms.Form):
    meal_id = forms.IntegerField(widget=forms.HiddenInput)
    food_library_item_id = forms.ChoiceField(choices=[])
    grams = forms.FloatField(min_value=0.01)
    order = forms.IntegerField(min_value=1)

    def __init__(self, *args, trainer_user=None, **kwargs):
        super().__init__(*args, **kwargs)

        if trainer_user is not None:
            foods = FoodLibraryItem.objects.filter(user=trainer_user).order_by("name")
            self.fields["food_library_item_id"].choices = [
                (food.id, food.name) for food in foods
            ]


class CreateCheckInFormForm(forms.Form):
    name = forms.CharField(max_length=255)
    form_type = forms.ChoiceField(choices=CheckInForm.FORM_TYPE_CHOICES)


class UpdateCheckInFormForm(forms.Form):
    name = forms.CharField(max_length=255)
    form_type = forms.ChoiceField(choices=CheckInForm.FORM_TYPE_CHOICES)
    is_active = forms.BooleanField(required=False)


class CreateCheckInQuestionForm(forms.Form):
    question_text = forms.CharField(max_length=255)
    question_type = forms.ChoiceField(choices=CheckInQuestion.QUESTION_TYPE_CHOICES)
    is_required = forms.BooleanField(required=False)
    order = forms.IntegerField(min_value=1)
    dropdown_options = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="For dropdown questions only. Put one option per line.",
    )


class UpdateCheckInQuestionForm(forms.Form):
    question_text = forms.CharField(max_length=255)
    question_type = forms.ChoiceField(choices=CheckInQuestion.QUESTION_TYPE_CHOICES)
    is_required = forms.BooleanField(required=False)
    order = forms.IntegerField(min_value=1)
    dropdown_options = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="For dropdown questions only. Put one option per line.",
    )
