from django import forms
from .models import User
from apps.workouts.models import WorkoutPlan


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

    def __init__(self, *args, trainer_user=None, **kwargs):
        super().__init__(*args, **kwargs)

        if trainer_user is not None:
            plans = WorkoutPlan.objects.filter(user=trainer_user).order_by("name")
            self.fields["workout_plan_id"].choices = [
                (plan.id, plan.name) for plan in plans
            ]


class CreateExerciseLibraryItemForm(forms.Form):
    name = forms.CharField(max_length=255)
    video_url = forms.URLField(required=False)
    coaching_notes = forms.CharField(required=False, widget=forms.Textarea)
