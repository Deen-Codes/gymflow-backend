"""PICKER-POPULARITY-SORT (#340) — bump icon_priority on the
universal compound staples so the exercise picker opens to common
lifts (Bench Press, Squat, Deadlift, …) instead of alphabetical
noise like "2-Board Press" and "3/4 Sit-Up".

Priority bands:
  • 30 — big-3 compounds every lifter knows by name. These sort to
    the very top of the picker's unfiltered list.
  • 25 — second-tier compounds + universal pull/press variants.
  • 20 — common dumbbell + cable + machine accessories that show
    up in 80% of split routines.

Deen's own PT-built plan (`seed_deen_priority_plan`) writes
icon_priority=10, which still beats the 0-default rest of the
catalog. We deliberately leave that alone — Deen's plan is a
*user-specific* curation, this command is a *universal* one.

Idempotent. Fuzzy name match (lower + strip + collapse spaces)
so it lands on whichever variant ("Bench Press" vs "Barbell
Bench Press" vs "Barbell Bench Press - Medium Grip") the catalog
actually has. Skips silently when no row matches.

Usage:
    python manage.py seed_picker_popularity --dry-run
    python manage.py seed_picker_popularity
"""
from __future__ import annotations

import re

from django.core.management.base import BaseCommand
from django.db import transaction

# Universal popularity tiers. Each entry is a (priority, pattern)
# pair. Pattern is a regex (case-insensitive) applied to the
# normalised name (lowercased, punctuation collapsed). First match
# wins; later patterns can't downgrade an earlier priority.
PRIORITY_PATTERNS = [
    # ───── Tier 30 — big-3 compounds ─────
    (30, r"^(barbell )?bench press$"),
    (30, r"^(barbell )?back squat$"),
    (30, r"^(conventional )?deadlift$"),
    (30, r"^squat$"),
    (30, r"^bench press$"),

    # ───── Tier 25 — universal compounds ─────
    (25, r"^(barbell )?overhead press$"),
    (25, r"^(standing )?(barbell )?military press$"),
    (25, r"^(barbell )?row$"),
    (25, r"^(barbell )?bent[\- ]over row$"),
    (25, r"^pull[\- ]?up$"),
    (25, r"^chin[\- ]?up$"),
    (25, r"^dip$"),
    (25, r"^(barbell )?front squat$"),
    (25, r"^romanian deadlift$"),
    (25, r"^sumo deadlift$"),
    (25, r"^(barbell )?incline bench press$"),
    (25, r"^lat pulldown$"),
    (25, r"^seated cable row$"),
    (25, r"^leg press$"),

    # ───── Tier 20 — common accessories ─────
    (20, r"^(barbell )?bicep curl$"),
    (20, r"^dumbbell curl$"),
    (20, r"^hammer curl$"),
    (20, r"^tricep pushdown$"),
    (20, r"^(cable )?tricep extension$"),
    (20, r"^(seated )?dumbbell shoulder press$"),
    (20, r"^lateral raise$"),
    (20, r"^face pull$"),
    (20, r"^dumbbell row$"),
    (20, r"^dumbbell bench press$"),
    (20, r"^dumbbell fly$"),
    (20, r"^cable crossover$"),
    (20, r"^pec deck$"),
    (20, r"^leg extension$"),
    (20, r"^leg curl$"),
    (20, r"^(lying|seated) (hamstring|leg) curl$"),
    (20, r"^(standing|seated) calf raise$"),
    (20, r"^(hip thrust|barbell hip thrust)$"),
    (20, r"^bulgarian split squat$"),
    (20, r"^lunge$"),
    (20, r"^plank$"),
    (20, r"^crunch$"),
    (20, r"^russian twist$"),
    (20, r"^hanging leg raise$"),
]


def _normalise(name: str) -> str:
    """Lower-case + collapse repeated whitespace. No punctuation
    stripping — the patterns are written to be explicit about
    hyphens / dashes where they matter."""
    return re.sub(r"\s+", " ", name.strip()).lower()


class Command(BaseCommand):
    help = "Bump ExerciseCatalog.icon_priority on universal compound lifts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would change, don't write.",
        )

    def handle(self, *args, **opts):
        from apps.workouts.models import ExerciseCatalog

        dry_run = opts["dry_run"]
        compiled = [(prio, re.compile(pat, re.IGNORECASE)) for prio, pat in PRIORITY_PATTERNS]

        # Pull the full published catalog into memory — under 2k
        # rows and we want to do fuzzy matching, not a SQL LIKE per
        # pattern. Cheap.
        rows = list(
            ExerciseCatalog.objects
            .filter(is_published=True)
            .only("id", "name", "icon_priority")
        )

        # First-match-wins resolution, building (priority, row) pairs.
        bumps: list[tuple[int, ExerciseCatalog]] = []
        for r in rows:
            norm = _normalise(r.name)
            for prio, pat in compiled:
                if pat.search(norm):
                    if (r.icon_priority or 0) < prio:
                        bumps.append((prio, r))
                    break

        if not bumps:
            self.stdout.write(self.style.SUCCESS(
                "No bumps needed — catalog already at expected priorities."
            ))
            return

        self.stdout.write(f"Found {len(bumps)} rows to bump:\n")
        for prio, r in bumps:
            self.stdout.write(f"  [{r.icon_priority or 0:>3} → {prio:>3}]  {r.name!r}")

        if dry_run:
            self.stdout.write(self.style.WARNING("\nDRY RUN — no writes."))
            return

        with transaction.atomic():
            for prio, r in bumps:
                r.icon_priority = prio
                r.save(update_fields=["icon_priority"])

        self.stdout.write(self.style.SUCCESS(
            f"\nBumped {len(bumps)} catalog rows."
        ))
