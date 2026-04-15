from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from apps.workouts.models import (
    WorkoutPlan,
    WorkoutDay,
    Exercise,
    ExerciseSetTarget,
)


WORKOUT_DATA = [
    {
        "title": "Upper",
        "exercises": [
            {"label": "A", "name": "Pec Deck Chest Fly", "reps": ["10-15", "10-15", "10-15"]},
            {"label": "B", "name": "Incline Machine Chest Press", "reps": ["6-10", "10-15"]},
            {"label": "C", "name": "Chest Supported T-Bar Row", "reps": ["6-10", "10-15"]},
            {"label": "D", "name": "Neutral Grip Lat Pull Down", "reps": ["8-12", "12-15", "12-15"]},
            {"label": "E", "name": "Torso Supported Lateral Raises", "reps": ["12-15", "12-15", "12-15"]},
            {"label": "F", "name": "Shoulder Press Machine", "reps": ["6-10", "10-15"]},
            {"label": "G1", "name": "Cross Body Tricep Extension", "reps": ["10-15", "10-15", "10-15"], "superset_group": 1},
            {"label": "G2", "name": "Dual Arm Cable Bicep Curl", "reps": ["10-15", "10-15", "10-15"], "superset_group": 1},
            {"label": "H1", "name": "Dumbbell Skull Crush", "reps": ["10-15", "10-15", "10-15"], "superset_group": 2},
            {"label": "H2", "name": "Single Arm Dumbbell Preacher Bicep Curl", "reps": ["10-15", "10-15", "10-15"], "superset_group": 2},
        ],
    },
    {
        "title": "Lower",
        "exercises": [
            {"label": "A", "name": "Seated Calf Raise", "reps": ["30", "20", "10"]},
            {"label": "B", "name": "Lying Hamstring Curl", "reps": ["10-15", "10-15", "10-15"]},
            {"label": "C", "name": "Leg Extension", "reps": ["8-12", "12-15"]},
            {"label": "D", "name": "Leg Press", "reps": ["6-10", "10-15"]},
            {"label": "E", "name": "Hack Squat", "reps": ["8-12", "12-15"]},
            {"label": "F", "name": "Dumbbell Bulgarian Split Squat", "reps": ["12-15", "12-15"]},
            {"label": "G", "name": "Adductor Machine", "reps": ["15-20", "15-20", "15-20"]},
        ],
    },
    {
        "title": "Push",
        "exercises": [
            {"label": "A", "name": "Incline Machine Chest Press", "reps": ["8-12", "12-15"]},
            {"label": "B", "name": "Dumbbell Shoulder Press", "reps": ["10-15", "10-15", "10-15"]},
            {"label": "C", "name": "Seated Machine Chest Press", "reps": ["6-10", "10-15"]},
            {"label": "D", "name": "Lying Cuffed Lateral Raises", "reps": ["8-12", "12-15", "15-20"]},
            {"label": "E", "name": "Cable Crossovers (Pec Fly)", "reps": ["10-15", "10-15", "10-15"]},
            {"label": "F", "name": "Machine Dip", "reps": ["6-10", "10-15"]},
            {"label": "G", "name": "SA Tricep Pushdown", "reps": ["10-15", "10-15", "10-15"]},
        ],
    },
    {
        "title": "Pull",
        "exercises": [
            {"label": "A", "name": "Reverse Bench SA Pulldown", "reps": ["12-15", "12-15", "12-15"]},
            {"label": "B", "name": "Hammer Strength Low to High Row", "reps": ["8-12", "12-15"]},
            {"label": "C", "name": "Chest Supported T-Bar Row", "reps": ["8-12", "10-15"]},
            {"label": "D", "name": "Neutral Grip Lat Pull Down", "reps": ["10-15", "10-15", "10-15"]},
            {"label": "E", "name": "Single Arm D-Handle Low Row", "reps": ["10-15", "10-15"]},
            {"label": "F", "name": "EZ Bar Cable Bicep Curls", "reps": ["10-15", "10-15", "10-15"]},
            {"label": "G", "name": "Alternating Hammer Curls", "reps": ["10-15", "10-15", "10-15"]},
        ],
    },
    {
        "title": "Legs",
        "exercises": [
            {"label": "A", "name": "Lying Hamstring Curl", "reps": ["6-10", "8-12"]},
            {"label": "B", "name": "Seated Hamstring Curl", "reps": ["8-12", "12-15"]},
            {"label": "C", "name": "Hack Squat", "reps": ["8-12", "10-15"]},
            {"label": "D", "name": "Glute Drive Machine", "reps": ["8-12", "12-15"]},
            {"label": "E", "name": "Leg Extension", "reps": ["12-15", "12-15", "12-15"]},
            {"label": "F", "name": "Seated Calf Raise", "reps": ["30", "20", "10"]},
        ],
    },
]


class Command(BaseCommand):
    help = "Seed Deen's workout plan into the database"

    def handle(self, *args, **options):
        User = get_user_model()
        username = "deen"

        user, created = User.objects.get_or_create(
            username=username,
            defaults={"email": "deen@example.com"},
        )

        if created:
            user.set_password("changeme123")
            user.save()
            self.stdout.write(self.style.WARNING("Created fallback user 'deen' with temporary password."))

        plan, _ = WorkoutPlan.objects.get_or_create(
            user=user,
            name="Deen Ali Training Plan",
            defaults={"is_active": True},
        )

        # Clear existing structure for this plan so reseeding stays clean
        plan.days.all().delete()

        for day_index, day_data in enumerate(WORKOUT_DATA, start=1):
            workout_day = WorkoutDay.objects.create(
                plan=plan,
                title=day_data["title"],
                order=day_index,
            )

            for exercise_index, exercise_data in enumerate(day_data["exercises"], start=1):
                exercise = Exercise.objects.create(
                    workout_day=workout_day,
                    label=exercise_data["label"],
                    name=exercise_data["name"],
                    order=exercise_index,
                    superset_group=exercise_data.get("superset_group"),
                )

                for set_index, rep_target in enumerate(exercise_data["reps"], start=1):
                    ExerciseSetTarget.objects.create(
                        exercise=exercise,
                        set_number=set_index,
                        reps=rep_target,
                    )

        self.stdout.write(self.style.SUCCESS("Workout plan seeded successfully."))
