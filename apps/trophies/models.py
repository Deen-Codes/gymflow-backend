"""Trophy catalogue + per-user awards.

Two-table design:

    Trophy            — the static catalogue (one row per defined trophy).
                        Seeded from `apps.trophies.seed.TROPHY_CATALOGUE`
                        via a data migration so the same 100 entries
                        exist on every install. Editable from /admin if
                        we ever want to tweak copy or icons without a
                        deploy.

    ClientTrophyAward — a row per (user, trophy) pair when the user
                        unlocks a trophy. Includes the timestamp so we
                        can show "Earned 14 May 2026" on the detail
                        sheet and order the collection by recency.

The criteria for unlocking each trophy are NOT stored on the row —
they live in `apps.trophies.evaluators` as Python code, because some
trophies have logic (e.g. "PR three weeks running") that doesn't fit
a JSON criteria spec without us inventing a half-baked DSL.
"""
from django.conf import settings
from django.db import models


class Trophy(models.Model):
    # Categories — kept loose strings (not FK) so adding a new category
    # is a one-line change in `seed.py`. Used for grouping in the iOS
    # collection view.
    CATEGORY_CHOICES = [
        ("workout_volume", "Workout Volume"),
        ("streaks",        "Streaks"),
        ("frequency",      "Frequency"),
        ("personal_record", "Personal Records"),
        ("reps_sets",      "Reps & Sets"),
        ("time_special",   "Time & Special Days"),
        ("check_ins",      "Check-ins & Progress"),
        ("nutrition",      "Nutrition & Hydration"),
        ("body",           "Body Composition"),
        ("onboarding",     "Onboarding"),
    ]

    # Rarity drives the card colour + sort weight in the iOS collection.
    RARITY_COMMON    = "common"
    RARITY_UNCOMMON  = "uncommon"
    RARITY_RARE      = "rare"
    RARITY_EPIC      = "epic"
    RARITY_LEGENDARY = "legendary"
    RARITY_CHOICES = [
        (RARITY_COMMON,    "Common"),
        (RARITY_UNCOMMON,  "Uncommon"),
        (RARITY_RARE,      "Rare"),
        (RARITY_EPIC,      "Epic"),
        (RARITY_LEGENDARY, "Legendary"),
    ]

    code        = models.CharField(max_length=80, unique=True)
    name        = models.CharField(max_length=100)
    description = models.CharField(max_length=240)
    category    = models.CharField(max_length=40, choices=CATEGORY_CHOICES)
    rarity      = models.CharField(max_length=20, choices=RARITY_CHOICES)
    # SF Symbol name used by iOS. Kept on the model so the catalogue
    # is the single source of truth — server-driven UI, no iOS update
    # needed when we add or rename a trophy.
    icon        = models.CharField(max_length=80)
    # Stable display order within a category, lower = earlier.
    sort_order  = models.IntegerField(default=0)

    class Meta:
        ordering = ["category", "sort_order", "id"]

    def __str__(self):
        return self.name


class ClientTrophyAward(models.Model):
    """One row per user per trophy — the user has earned the trophy."""
    user      = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="trophy_awards",
    )
    trophy    = models.ForeignKey(Trophy, on_delete=models.CASCADE)
    earned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "trophy"],
                name="unique_user_trophy",
            ),
        ]
        indexes = [
            # Recent-first listing on the iOS Trophies tab.
            models.Index(fields=["user", "-earned_at"]),
        ]

    def __str__(self):
        return f"{self.user} - {self.trophy.name} ({self.earned_at:%Y-%m-%d})"
