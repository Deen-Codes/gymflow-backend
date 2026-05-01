"""
Phase A — AI-driven mutations to the user's plan + nutrition.

Two models:

  • WorkoutMutation  — proposed/applied changes to the user's
                        assigned workout plan (swap exercise,
                        change set scheme, reorder days, deload,
                        add/remove a day).
  • NutritionMutation — proposed/applied changes to the user's
                        macro targets / preferences / meal frequency.

Both are AUDIT TRAILS by design. The AI's `propose_*` tool calls
write rows here in `proposed` state; the iOS Apply button hits
the apply endpoint which validates + flips status to `applied` AND
mutates the canonical row (SoloProfile, WorkoutDay, etc.). Decline
flips status to `declined` without mutating anything else.

Why the separate audit table rather than mutating in place from
the AI directly:

  1. Behavioural — the user's locus of control stays intact. The
     AI proposes; the user decides. This is the structural
     difference between coaching and command-and-control. Per
     SDT (Deci & Ryan) — autonomy support is the dominant
     adherence lever.

  2. Reversibility — every applied change carries the original
     value, so Profile → "AI changes" can offer a "revert" pill.

  3. Tunability — refusal patterns + accept rates per mutation
     kind are visible in the audit data. Lets us tighten the
     system prompt against patterns the AI proposes that users
     keep declining.

  4. Trust — the user never wakes up to "the AI changed my
     programme without asking". Every applied mutation has a
     trail showing rationale + timestamp + user click.

Hard floors are enforced at the tool-handler level BEFORE a row
is even created (so refused proposals don't pollute the audit
trail). Apply also re-validates the floors as defense-in-depth.

Schema:
  • status: proposed → (applied | declined | expired)
  • original_value / new_value JSON: enough to render a diff
    later in Profile and to power a revert action.
  • ai_rationale: the human-facing explanation the AI gave.
    Stored separately so Profile's review screen can show
    just the trade-off without the rest of the conversation.
"""
from django.db import models

from .models import User


# --------------------------------------------------------------------
# Shared status enum
# --------------------------------------------------------------------


class MutationStatus(models.TextChoices):
    """Lifecycle:
        proposed → applied (terminal)
        proposed → declined (terminal)
        proposed → expired (terminal — for stale proposals never
            actioned; reserved for a future cleanup job, not used
            tonight).
    """
    PROPOSED = "proposed", "Proposed"
    APPLIED  = "applied",  "Applied"
    DECLINED = "declined", "Declined"
    EXPIRED  = "expired",  "Expired"


# --------------------------------------------------------------------
# Workout mutations
# --------------------------------------------------------------------


class WorkoutMutation(models.Model):
    """Audit row for an AI-proposed change to the user's workout plan.

    `kind` partitions into the taxonomy in
    `AI_MUTATIONS_RESEARCH.md` §4.1. The `payload` JSON shape is
    kind-specific — validated by the tool handler before the row
    is created. We keep it loose JSON rather than per-kind columns
    because new kinds will land over time and migrations for each
    are wasteful.
    """

    KIND_SWAP_EXERCISE     = "swap_exercise"
    KIND_CHANGE_SET_SCHEME = "change_set_scheme"
    KIND_REORDER_DAYS      = "reorder_days"
    KIND_DELOAD_WEEK       = "deload_week"
    KIND_ADD_DAY           = "add_day"
    KIND_REMOVE_DAY        = "remove_day"
    KIND_CHOICES = [
        (KIND_SWAP_EXERCISE,     "Swap exercise"),
        (KIND_CHANGE_SET_SCHEME, "Change set scheme"),
        (KIND_REORDER_DAYS,      "Reorder days"),
        (KIND_DELOAD_WEEK,       "Deload week"),
        (KIND_ADD_DAY,           "Add day"),
        (KIND_REMOVE_DAY,        "Remove day"),
    ]

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="workout_mutations",
    )

    kind   = models.CharField(max_length=24, choices=KIND_CHOICES)
    status = models.CharField(
        max_length=10, choices=MutationStatus.choices,
        default=MutationStatus.PROPOSED,
    )

    # Diff data — enough to render the proposal card AND to power
    # a future revert. Both stored as JSON.
    original_value = models.JSONField(default=dict, blank=True)
    new_value      = models.JSONField(default=dict, blank=True)

    # The human-facing trade-off the AI gave. Drives the proposal
    # card body. ≤500 chars enforced at the tool layer.
    ai_rationale = models.TextField(blank=True, default="")

    # Audit timestamps.
    proposed_at = models.DateTimeField(auto_now_add=True)
    decided_at  = models.DateTimeField(null=True, blank=True)
    applied_at  = models.DateTimeField(null=True, blank=True)

    # The chat turn this came from — useful for analytics +
    # debugging. Stored as a free-form string (a UUID iOS
    # generates) so we don't need a foreign key into a
    # not-yet-existent ChatTurn table.
    chat_turn_ref = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        ordering = ["-proposed_at"]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["user", "-proposed_at"]),
        ]

    def __str__(self):
        return f"WorkoutMutation({self.kind} {self.status} u={self.user_id})"


# --------------------------------------------------------------------
# Nutrition mutations
# --------------------------------------------------------------------


class NutritionMutation(models.Model):
    """Audit row for an AI-proposed change to the user's nutrition.

    Same shape as WorkoutMutation. Kinds are nutrition-specific:
    macro adjustments, dietary preferences (exclude/include
    food families), meal-frequency tweaks.
    """

    KIND_ADJUST_MACROS    = "adjust_macros"
    KIND_SWAP_PREFERENCE  = "swap_preference"
    KIND_CHANGE_MEAL_FREQ = "change_meal_freq"
    KIND_CHOICES = [
        (KIND_ADJUST_MACROS,    "Adjust macros"),
        (KIND_SWAP_PREFERENCE,  "Swap preference"),
        (KIND_CHANGE_MEAL_FREQ, "Change meal frequency"),
    ]

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="nutrition_mutations",
    )

    kind   = models.CharField(max_length=24, choices=KIND_CHOICES)
    status = models.CharField(
        max_length=10, choices=MutationStatus.choices,
        default=MutationStatus.PROPOSED,
    )

    original_value = models.JSONField(default=dict, blank=True)
    new_value      = models.JSONField(default=dict, blank=True)

    ai_rationale = models.TextField(blank=True, default="")

    proposed_at = models.DateTimeField(auto_now_add=True)
    decided_at  = models.DateTimeField(null=True, blank=True)
    applied_at  = models.DateTimeField(null=True, blank=True)

    chat_turn_ref = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        ordering = ["-proposed_at"]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["user", "-proposed_at"]),
        ]

    def __str__(self):
        return f"NutritionMutation({self.kind} {self.status} u={self.user_id})"
