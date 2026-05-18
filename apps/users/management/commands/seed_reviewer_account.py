"""APPLE-REVIEW-BYPASS + TEST-ACCOUNTS — provision the four test
accounts used by App Store review AND by Deen's day-to-day QC.

Four accounts, all seeded against today's date and all idempotent
(running the command again wipes + re-seeds against the current day):

  reviewer@afletics.com  — Pro AI tier, ~30 days of history. Used
                            by App Store review reviewers.
  day0@afletics.com      — Pro AI tier, NO history. The cold-start
                            test account. Tests every empty state.
  day1@afletics.com      — Pro AI tier, exactly ONE day of data
                            (today). Tests "single data point" UI
                            where comparisons aren't yet possible
                            but data exists.
  reset@afletics.com     — Pro AI tier, NO history. Same as day0,
                            but the magic-link verify view wipes
                            anything the user logged during a
                            session BEFORE issuing the next token.
                            Lets Deen test the new-user experience
                            repeatedly without re-creating accounts.

Run once on the deploy (and on every redeploy via build.sh):

    python manage.py seed_reviewer_account

Bypass route — each account has its own derived token from a single
env var so reviewers + Deen don't need 4 separate secrets:

  reviewer:  APPLE_REVIEW_TOKEN
  day0:      "{APPLE_REVIEW_TOKEN}-day0"
  day1:      "{APPLE_REVIEW_TOKEN}-day1"
  reset:     "{APPLE_REVIEW_TOKEN}-reset"

Pasted into the magic-link verify endpoint, these resolve to the
matching account. The reset variant also calls
`wipe_test_account_history(user)` (in test_account_seeds.py) before
issuing the token so the account starts fresh on every login.

The command name is preserved (seed_reviewer_account) so the
existing build.sh hook keeps working. Real user accounts are NEVER
touched — every query is filtered by the test emails above.
"""
from django.conf import settings
from django.core.management.base import BaseCommand

from apps.users.test_account_seeds import (
    TestAccountSpec,
    provision_test_account,
)


REVIEWER_EMAIL_DEFAULT = "reviewer@afletics.com"


class Command(BaseCommand):
    help = "Create / refresh the four App Store + QC test accounts."

    def handle(self, *args, **options):
        reviewer_email = getattr(settings, "APPLE_REVIEW_EMAIL", REVIEWER_EMAIL_DEFAULT)
        token = getattr(settings, "APPLE_REVIEW_TOKEN", None)

        if not token:
            self.stderr.write(
                "APPLE_REVIEW_TOKEN env var is unset on this deploy. The four "
                "test accounts will be provisioned, but the bypass route is "
                "closed until you set the env var."
            )

        specs = [
            TestAccountSpec(
                email=reviewer_email,
                first_name="Apple",
                last_name="Reviewer",
                history_mode="full",          # ~30 days of data
                assign_programme="Starting Strength",
                days_per_week=3,
            ),
            TestAccountSpec(
                email="day0@afletics.com",
                first_name="Day0",
                last_name="Test",
                history_mode="none",          # cold-start empty
                assign_programme=None,        # no programme = "Pick a programme" CTA
                days_per_week=3,
            ),
            TestAccountSpec(
                email="day1@afletics.com",
                first_name="Day1",
                last_name="Test",
                history_mode="single_day",    # 1 workout + 1 weight, both today
                assign_programme="Starting Strength",
                days_per_week=3,
            ),
            TestAccountSpec(
                email="reset@afletics.com",
                first_name="Reset",
                last_name="Test",
                history_mode="none",          # starts empty
                assign_programme=None,
                days_per_week=3,
            ),
        ]

        for spec in specs:
            user, summary = provision_test_account(spec)
            self.stdout.write(self.style.SUCCESS(
                f"{spec.email} (user_id={user.id}): {summary}"
            ))

        if token:
            self.stdout.write("")
            self.stdout.write("Bypass URLs (paste into Safari on the test device):")
            self.stdout.write(f"  reviewer: https://afletics.com/magic/{token}/")
            self.stdout.write(f"  day0:     https://afletics.com/magic/{token}-day0/")
            self.stdout.write(f"  day1:     https://afletics.com/magic/{token}-day1/")
            self.stdout.write(f"  reset:    https://afletics.com/magic/{token}-reset/  (wipes on sign-in)")
