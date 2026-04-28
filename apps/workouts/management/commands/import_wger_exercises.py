"""Bulk-import exercises from the wger public API into ExerciseCatalog.

The wger API:
    Base URL:   https://wger.de/api/v2/
    Endpoint:   /exerciseinfo/?language=2&limit=...&offset=...
    Auth:       none required for read-only public data
    Rate limit: ~50 req/min unauthenticated; we sleep 0.4s between
                pages to stay well under it.

Idempotent: re-runs `update_or_create` keyed on (source='wger', external_id=<wger id>)
so re-imports refresh existing rows instead of duplicating them.

Usage:
    python manage.py import_wger_exercises
    python manage.py import_wger_exercises --limit 50   # sample
    python manage.py import_wger_exercises --dry-run    # log only

Network: this command makes outbound HTTPS calls. If your deploy host
blocks egress, run it locally and dump/load fixtures instead.
"""
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import json

from django.core.management.base import BaseCommand

from apps.workouts.models import ExerciseCatalog


WGER_BASE = "https://wger.de/api/v2"
LANGUAGE_ENGLISH = 2
PAGE_SIZE = 100
SLEEP_BETWEEN_PAGES = 0.4


def _fetch_page(offset: int, page_size: int):
    # Note: dropped the `language=2` filter — that was filtering the
    # whole exercise to only those with an English translation, which
    # turned out to silently exclude several hundred otherwise-valid
    # exercises. We now pull the whole catalogue and pick the best
    # available translation per record in `_pick_translation`.
    qs = urlencode({
        "limit":  page_size,
        "offset": offset,
        "status": 2,    # published exercises only
    })
    url = f"{WGER_BASE}/exerciseinfo/?{qs}"
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _pick_translation(translations):
    """Pick a translation for an exercise, preferring English but
    falling back to whatever else is available. wger sometimes ships
    exercises with only German or Spanish translations — previously
    those were silently dropped, which is why earlier imports had
    fewer rows than the upstream catalogue.

    Returns (name, description) with HTML stripped. Returns
    ("", "") only if there are zero translations at all (extremely
    rare — usually a placeholder draft record we wouldn't want anyway).
    """
    translations = translations or []
    # First pass — prefer English.
    for t in translations:
        if t.get("language") == LANGUAGE_ENGLISH:
            name = (t.get("name") or "").strip()
            if name:
                return name, _strip_html(t.get("description") or "").strip()
    # Second pass — accept any translation with a non-empty name.
    for t in translations:
        name = (t.get("name") or "").strip()
        if name:
            return name, _strip_html(t.get("description") or "").strip()
    return "", ""


def _strip_html(html: str) -> str:
    """Crude tag stripper — wger descriptions are simple HTML, no
    point pulling in beautifulsoup just for this."""
    out = []
    in_tag = False
    for ch in html:
        if ch == "<":
            in_tag = True
        elif ch == ">":
            in_tag = False
        elif not in_tag:
            out.append(ch)
    return "".join(out)


def _muscle_label(muscles):
    """Pick the first muscle (wger flags primary muscles separately,
    but `muscles` is the high-confidence list)."""
    if not muscles:
        return ""
    first = muscles[0] or {}
    return (first.get("name_en") or first.get("name") or "").strip()


def _equipment_label(equipment_list):
    if not equipment_list:
        return "Bodyweight"
    return (equipment_list[0].get("name") or "").strip()


def _image_url(images):
    for image in images or []:
        if image.get("is_main"):
            return image.get("image") or ""
    if images:
        return images[0].get("image") or ""
    return ""


class Command(BaseCommand):
    help = "Import published exercises from the wger public API."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Stop after importing this many records (useful for sampling).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be imported but do not write to the DB.",
        )

    def handle(self, *args, **options):
        max_records = options["limit"]
        dry_run = options["dry_run"]

        offset = 0
        created = 0
        updated = 0
        skipped = 0
        seen = 0

        # Per-page progress so the user sees the import ticking
        # through rather than staring at a silent terminal for 60s.
        # Force-flush each line because Render's shell buffers stdout
        # heavily until newline + flush.
        def log(msg):
            self.stdout.write(msg)
            self.stdout.flush()

        log(f"Fetching wger catalog from {WGER_BASE}/exerciseinfo/ …")

        while True:
            try:
                page = _fetch_page(offset, PAGE_SIZE)
            except (HTTPError, URLError) as exc:
                self.stderr.write(f"Network error at offset={offset}: {exc}")
                return

            results = page.get("results") or []
            total = page.get("count")
            if not results:
                break

            log(
                f"  page offset={offset} got {len(results)} records "
                f"(total upstream: {total or '?'}) — created={created} "
                f"updated={updated} skipped={skipped}"
            )

            for record in results:
                seen += 1
                if max_records and (created + updated) >= max_records:
                    self.stdout.write(self.style.SUCCESS(
                        f"Hit --limit={max_records}, stopping. "
                        f"created={created} updated={updated} skipped={skipped} seen={seen}"
                    ))
                    return

                external_id = str(record.get("id") or "")
                name, description = _pick_translation(record.get("translations"))
                if not name:
                    skipped += 1
                    continue

                muscle = _muscle_label(record.get("muscles"))
                equipment = _equipment_label(record.get("equipment"))
                image_url = _image_url(record.get("images"))

                if dry_run:
                    self.stdout.write(
                        f"[dry-run] {external_id} {name} | {muscle} | {equipment}"
                    )
                    continue

                _, was_created = ExerciseCatalog.objects.update_or_create(
                    source=ExerciseCatalog.SOURCE_WGER,
                    external_id=external_id,
                    defaults={
                        "name": name,
                        "muscle_group": muscle,
                        "equipment": equipment,
                        "instructions": description,
                        "image_url": image_url,
                        "is_published": True,
                    },
                )
                if was_created:
                    created += 1
                else:
                    updated += 1

            if not page.get("next"):
                break
            offset += PAGE_SIZE
            time.sleep(SLEEP_BETWEEN_PAGES)

        self.stdout.write(self.style.SUCCESS(
            f"wger import done. created={created} updated={updated} "
            f"skipped={skipped} seen={seen}"
        ))
