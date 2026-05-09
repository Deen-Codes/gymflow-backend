"""EXERCISE-LIB-WGER-PURGE (#211) — delete AGPL wger rows.

A previous `import_wger_exercises` run left ~896 AGPL-licensed rows
in ExerciseCatalog. AGPL is viral copyleft — keeping wger data in
our catalog DB would force the entire catalog under AGPL, which
breaks our commercial position.

The current `import_exercise_catalog` deliberately blocks wger as a
source. This command cleans up legacy rows.

Idempotent — safe to run multiple times.

Usage:
    python manage.py purge_wger_exercises --dry-run
    python manage.py purge_wger_exercises
"""
from django.core.management.base import BaseCommand

from apps.workouts.models import ExerciseCatalog


class Command(BaseCommand):
    help = "Delete all wger-source rows from ExerciseCatalog (AGPL licensing)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show counts only, don't delete.",
        )

    def handle(self, *args, **opts):
        dry_run = opts["dry_run"]

        qs = ExerciseCatalog.objects.filter(source=ExerciseCatalog.SOURCE_WGER)
        count = qs.count()

        self.stdout.write(f"Found {count} wger rows in ExerciseCatalog.")

        if count == 0:
            self.stdout.write(self.style.SUCCESS("Catalog is already wger-free."))
            return

        # Show breakdown so we can see what we're about to nuke
        from collections import Counter
        equip = Counter(qs.values_list("equipment", flat=True))
        self.stdout.write("\nEquipment breakdown of wger rows:")
        for k, v in sorted(equip.items(), key=lambda x: -x[1]):
            self.stdout.write(f"  {v:4} {k!r}")

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f"\nDry-run — would delete {count} rows. No DB writes."
            ))
            return

        deleted, _ = qs.delete()
        self.stdout.write(self.style.SUCCESS(
            f"\nDeleted {deleted} wger rows. Catalog is now AGPL-free."
        ))
