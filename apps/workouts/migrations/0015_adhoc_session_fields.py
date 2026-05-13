"""V0-LIMIT-3 — schema support for ad-hoc (plan-less) workout
sessions logged from the iOS as-you-go flow.

Changes:
  • WorkoutSession.workout_day  → nullable + on_delete=SET_NULL.
    Plan-mode sessions still FK to a WorkoutDay (NOT NULL in
    practice for those rows). Ad-hoc sessions have no workout_day.
  • WorkoutSession.title         → new CharField for ad-hoc session
    naming ("Push session", "Today", whatever the user / chip set).
    Plan-mode sessions can leave this blank — the workout_day's
    title is the source of truth there.
  • ExerciseSession.exercise     → nullable + SET_NULL. Ad-hoc
    lifts don't have a planned Exercise row to FK against; we
    record the catalog FK + free-form name instead.
  • ExerciseSession.name         → new CharField storing the lift's
    display name at log time. Mandatory for ad-hoc rows; empty for
    plan-mode rows where `exercise.name` is the source.
  • ExerciseSession.catalog      → new optional FK to
    ExerciseCatalog. Set when the ad-hoc lift was picked from the
    catalog (animation_url + form copy linkage). Null when the
    user typed a free-form name (rare for v0).

All additions are non-breaking — existing plan-mode session
creation continues to work unchanged. The new ad-hoc create
endpoint populates the new fields.
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("workouts", "0014_exercisecatalog_icon_priority"),
    ]

    operations = [
        # WorkoutSession.workout_day — was non-null CASCADE.
        # Make it nullable + SET_NULL so deleting the source plan
        # day doesn't wipe the user's historical session record.
        migrations.AlterField(
            model_name="workoutsession",
            name="workout_day",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="workouts.workoutday",
            ),
        ),

        # WorkoutSession.title — name for ad-hoc sessions.
        migrations.AddField(
            model_name="workoutsession",
            name="title",
            field=models.CharField(blank=True, default="", max_length=255),
        ),

        # ExerciseSession.exercise — was non-null CASCADE.
        # Make it nullable + SET_NULL so ad-hoc lifts (no Exercise
        # row to FK to) can persist.
        migrations.AlterField(
            model_name="exercisesession",
            name="exercise",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="workouts.exercise",
            ),
        ),

        # ExerciseSession.name — captured at log time so historical
        # rows survive an Exercise rename / delete.
        migrations.AddField(
            model_name="exercisesession",
            name="name",
            field=models.CharField(blank=True, default="", max_length=255),
        ),

        # ExerciseSession.catalog — optional FK to ExerciseCatalog
        # for ad-hoc lifts picked from the catalog picker. Lets the
        # historical record link back to animation_url + form copy.
        migrations.AddField(
            model_name="exercisesession",
            name="catalog",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="exercise_sessions",
                to="workouts.exercisecatalog",
            ),
        ),
    ]
