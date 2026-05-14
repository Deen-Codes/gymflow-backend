"""APPLE-REVIEW-BYPASS — provision the reviewer-only test account.

App Store review reviewers cannot receive magic-link emails, so the
auth flow has a special bypass: when the magic-link verify endpoint
receives a token equal to the `APPLE_REVIEW_TOKEN` env var, it signs
in as a pre-seeded reviewer user. This command provisions that user.

Run once on the deploy that serves App Review traffic:

    python manage.py seed_reviewer_account

Idempotent — running it again refreshes the profile state without
deleting any existing workout/nutrition logs the reviewer may have
generated while exploring the app.

Set these env vars on the deploy:
  APPLE_REVIEW_TOKEN  — the secret token the reviewer pastes.
                        Long, random, rotate after each review cycle.
  APPLE_REVIEW_EMAIL  — defaults to reviewer@gymflow.coach. Override
                        only if you want a different label.
"""
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.users.models import User, SoloProfile


REVIEWER_EMAIL_DEFAULT = "reviewer@gymflow.coach"
REVIEWER_FIRST_NAME = "Apple"
REVIEWER_LAST_NAME = "Reviewer"


class Command(BaseCommand):
    help = "Create or refresh the App Store review-only test account."

    def handle(self, *args, **options):
        email = getattr(settings, "APPLE_REVIEW_EMAIL", REVIEWER_EMAIL_DEFAULT)
        token = getattr(settings, "APPLE_REVIEW_TOKEN", None)

        if not token:
            self.stderr.write(
                "APPLE_REVIEW_TOKEN env var is unset on this deploy. "
                "The reviewer account will be provisioned, but until you set "
                "the env var the bypass route is closed."
            )

        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                "username":   email,
                "first_name": REVIEWER_FIRST_NAME,
                "last_name":  REVIEWER_LAST_NAME,
                "role":       User.SOLO,
                "is_active":  True,
            },
        )

        if not created:
            # Refresh identity fields in case they drifted, but keep the
            # row stable so existing related data (workouts, photos)
            # survives.
            updated = False
            if user.role != User.SOLO:
                user.role = User.SOLO
                updated = True
            if not user.is_active:
                user.is_active = True
                updated = True
            if updated:
                user.save(update_fields=["role", "is_active"])

        # Random unusable password — auth happens via the bypass token,
        # not a password. set_unusable_password() puts the row into a
        # state where check_password() always returns False.
        if user.has_usable_password():
            user.set_unusable_password()
            user.save(update_fields=["password"])

        # Provision (or refresh) the SoloProfile so the reviewer lands
        # on a Pro-AI-tier account with realistic onboarding answers.
        # Pro AI tier so the reviewer can exercise the whole feature
        # surface, including Smart Assist, without hitting a paywall
        # mid-review (which would block their checklist).
        profile, _ = SoloProfile.objects.get_or_create(user=user)
        profile.goals      = ["build_muscle", "get_stronger"]
        profile.experience = "one_to_three"
        profile.equipment  = "full_gym"
        profile.days_per_week = 4
        profile.gender     = "prefer_not"
        profile.tier       = SoloProfile.TIER_PRO_AI
        profile.tier_active_until = None  # active indefinitely for review
        profile.trial_started_at  = profile.trial_started_at or timezone.now()
        profile.save()

        if created:
            self.stdout.write(self.style.SUCCESS(
                f"Created reviewer account {email} (user_id={user.id}) on Pro AI tier."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"Refreshed reviewer account {email} (user_id={user.id}) on Pro AI tier."
            ))

        if token:
            self.stdout.write(
                "Reviewer bypass route is OPEN. In App Store Connect → App "
                "Review Information → Notes, tell the reviewer to open "
                f"https://gymflow.coach/magic/{token}/ in Safari on the device."
            )
