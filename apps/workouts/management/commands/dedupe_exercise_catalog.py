"""EXERCISE-LIB-1500 — one-shot dedup cleanup.

After the initial Free Exercise DB import, the catalog has 873
freedb rows + 43 Afletics-curated rows, with ~40 duplicates (e.g.
"Goblet Squat" exists in both sources). This command deletes the
freedb duplicates so the Afletics-curated entry is the canonical
one (UK-friendly naming).

Idempotent — safe to run multiple times. Deletes nothing if the
catalog is already clean.

Usage:
    python manage.py dedupe_exercise_catalog
    python manage.py dedupe_exercise_catalog --dry-run
"""
from django.core.management.base import BaseCommand

from apps.workouts.models import ExerciseCatalog
from apps.workouts.management.commands.import_exercise_catalog import (
    AFLETICS_FREEDB_DUPES,
    FREEDB_DUPE_IDS,
)


class Command(BaseCommand):
    help = "Delete Free Exercise DB rows that duplicate Afletics-curated entries."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List rows that would be deleted, don't actually delete.",
        )

    def handle(self, *args, **opts):
        dry_run = opts["dry_run"]

        qs = ExerciseCatalog.objects.filter(
            source=ExerciseCatalog.SOURCE_FREE_EXERCISE_DB,
            external_id__in=FREEDB_DUPE_IDS,
        )
        count = qs.count()

        self.stdout.write(
            f"Found {count} Free Exercise DB rows that duplicate "
            f"Afletics-curated entries."
        )

        if count == 0:
            self.stdout.write(self.style.SUCCESS("Catalog is clean — nothing to delete."))
            return

        # Show the mapping so it's reviewable
        self.stdout.write("\nMapping (Afletics-curated kept, FreeDB rows deleted):")
        for canonical, dupe_ids in sorted(AFLETICS_FREEDB_DUPES.items()):
            for fid in dupe_ids:
                row = qs.filter(external_id=fid).first()
                if row:
                    self.stdout.write(f"  {canonical:35} ↔ {row.name} ({fid})")

        if dry_run:
            self.stdout.write(self.style.WARNING("\nDry-run — no DB writes."))
            return

        deleted_count, _ = qs.delete()
        self.stdout.write(
            self.style.SUCCESS(
                f"\nDeleted {deleted_count} duplicate Free Exercise DB rows."
            )
        )
