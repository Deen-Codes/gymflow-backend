from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    TRAINER = "trainer"
    CLIENT = "client"

    ROLE_CHOICES = [
        (TRAINER, "Trainer"),
        (CLIENT, "Client"),
    ]

    role = models.CharField(max_length=20, choices=ROLE_CHOICES)

    def __str__(self):
        return f"{self.username} ({self.role})"


class TrainerProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="trainer_profile")
    business_name = models.CharField(max_length=255, blank=True)
    slug = models.SlugField(unique=True)

    # Phase 7.7.1 — Stripe Connect. Populated after the trainer
    # completes OAuth at /payments/oauth/connect/. Empty = not
    # connected. We never store secrets here, only the connected
    # account ID (acct_…) which is safe to keep in the DB.
    stripe_user_id = models.CharField(max_length=64, blank=True, default="")

    def __str__(self):
        return self.business_name or self.user.username

    @property
    def stripe_connected(self) -> bool:
        return bool(self.stripe_user_id)


class ClientProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="client_profile")
    trainer = models.ForeignKey(
        TrainerProfile,
        on_delete=models.CASCADE,
        related_name="clients"
    )
    assigned_workout_plan = models.ForeignKey(
        "workouts.WorkoutPlan",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_clients"
    )
    assigned_nutrition_plan = models.ForeignKey(
        "nutrition.NutritionPlan",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_clients"
    )

    def __str__(self):
        return self.user.username
