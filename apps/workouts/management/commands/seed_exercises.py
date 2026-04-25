"""Idempotently seed the global ExerciseCatalog with the curated list.

Usage:
    python manage.py seed_exercises
    python manage.py seed_exercises --replace   # nuke curated rows first

Run order on a fresh deploy:
    1. python manage.py migrate
    2. python manage.py seed_exercises          # ~40 staples
    3. python manage.py import_wger_exercises   # ~800 from wger
"""
import json
from pathlib import Path

from django.core.management.base import BaseCommand

from apps.workouts.models import ExerciseCatalog


class Command(BaseCommand):
    help = "Seed the global ExerciseCatalog with curated staple exercises."

    def add_arguments(self, parser):
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Delete existing curated rows before seeding (DOES NOT touch wger imports).",
        )

    def handle(self, *args, **options):
        data_path = Path(__file__).parent / "data" / "seed_exercises.json"
        if not data_path.exists():
            self.stderr.write(f"seed_exercises.json not found at {data_path}")
            return

        records = json.loads(data_path.read_text(encoding="utf-8"))
        self.stdout.write(f"Loaded {len(records)} curated exercises from JSON.")

        if options["replace"]:
            deleted, _ = ExerciseCatalog.objects.filter(
                source=ExerciseCatalog.SOURCE_CURATED
            ).delete()
            self.stdout.write(f"Deleted {deleted} existing curated rows.")

        created, updated = 0, 0
        for record in records:
            obj, was_created = ExerciseCatalog.objects.update_or_create(
                source=ExerciseCatalog.SOURCE_CURATED,
                name=record["name"],
                defaults={
                    "muscle_group": record.get("muscle_group", ""),
                    "equipment": record.get("equipment", ""),
                    "instructions": record.get("instructions", ""),
                    "video_url": record.get("video_url", ""),
                    "image_url": record.get("image_url", ""),
                    "is_published": True,
                    "external_id": "",
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1

        self.stdout.write(self.style.SUCCESS(
            f"Seed complete. created={created} updated={updated}"
        ))
