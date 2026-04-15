from django.contrib import admin
from .models import (
    WorkoutPlan,
    WorkoutDay,
    Exercise,
    ExerciseSetTarget,
    WorkoutSession,
    ExerciseSession,
    SetPerformance,
)


class ExerciseSetTargetInline(admin.TabularInline):
    model = ExerciseSetTarget
    extra = 0


@admin.register(Exercise)
class ExerciseAdmin(admin.ModelAdmin):
    list_display = ("name", "workout_day", "label", "order", "superset_group")
    list_filter = ("workout_day",)
    inlines = [ExerciseSetTargetInline]


class ExerciseInline(admin.TabularInline):
    model = Exercise
    extra = 0
    show_change_link = True


@admin.register(WorkoutDay)
class WorkoutDayAdmin(admin.ModelAdmin):
    list_display = ("title", "plan", "order")
    list_filter = ("plan",)
    inlines = [ExerciseInline]


@admin.register(WorkoutPlan)
class WorkoutPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "is_active")


class SetPerformanceInline(admin.TabularInline):
    model = SetPerformance
    extra = 0


@admin.register(ExerciseSession)
class ExerciseSessionAdmin(admin.ModelAdmin):
    list_display = ("workout_session", "exercise")
    inlines = [SetPerformanceInline]


@admin.register(WorkoutSession)
class WorkoutSessionAdmin(admin.ModelAdmin):
    list_display = ("user", "workout_day", "completed_at", "duration")


@admin.register(SetPerformance)
class SetPerformanceAdmin(admin.ModelAdmin):
    list_display = ("exercise_session", "set_number", "weight", "reps")
