"""EXERCISE-COPY-WHY backfill — populate `primary_benefit` via Claude.

Why this command — the form_copy YAMLs cover staples by hand, but
the long tail of the ~1,500-row catalog needs "why this lift?" copy
too, and writing 1,500 sentences manually isn't a good use of
anyone's time. This command queries Claude for a short coach-voice
paragraph (2–3 sentences) per exercise that hasn't been written yet,
and writes it back to ExerciseCatalog.primary_benefit.

Cost — at Haiku 4.5 prices, ~1,500 rows × ~300 tokens × $0.80/1M
input + ~150 tokens × $4/1M output ≈ $1.50 total for the full
catalog. A dry-run prints the proposal without writing.

Re-runs are safe: rows that already have copy are skipped unless
--overwrite. Idempotent if interrupted (per-row writes commit
immediately so a Ctrl-C resumes cleanly on the next run).

Usage:
    python manage.py backfill_exercise_benefits --dry-run --limit 5
    python manage.py backfill_exercise_benefits --limit 50
    python manage.py backfill_exercise_benefits --overwrite
"""
from __future__ import annotations

import json
import os
import time

import requests
from django.core.management.base import BaseCommand, CommandError

from apps.workouts.models import ExerciseCatalog


ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
# Haiku is plenty for short copy and ~5× cheaper than Sonnet. If we
# ever want richer voice we can swap to claude-sonnet-4-6 — same
# request shape, just a one-line change.
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """You are a senior strength coach writing short \
"why this lift is worth doing" copy for a fitness app's exercise \
catalogue. UK gym vocabulary. Coach voice — direct, warm, no fluff. \
Plain prose, no headers, no markdown, no bullet lists.

For each exercise the user shows you, return ONLY a single JSON \
object with one key:

  {"primary_benefit": "<2 to 3 sentences>"}

The copy should answer: what does this movement develop, what \
pattern does it train, and what role does it play in a typical \
training week. Avoid generic phrases like "great exercise" or \
"works your whole body". Be specific about muscles and movement. \
Keep it to 200 characters or fewer.

No preamble, no explanation outside the JSON. No code fences."""


class Command(BaseCommand):
    help = "Backfill ExerciseCatalog.primary_benefit via Claude."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Process at most N rows. Default: all rows missing primary_benefit.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print proposed copy, do not write to DB.",
        )
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Re-generate even for rows that already have primary_benefit.",
        )
        parser.add_argument(
            "--sleep",
            type=float,
            default=0.4,
            help="Seconds to sleep between API calls (rate-limit cushion).",
        )

    def handle(self, *args, **opts):
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise CommandError(
                "ANTHROPIC_API_KEY not set. Export it (or use the same env "
                "Render uses) before running."
            )

        qs = ExerciseCatalog.objects.all().order_by("-icon_priority", "name")
        if not opts["overwrite"]:
            qs = qs.filter(primary_benefit="")
        if opts["limit"]:
            qs = qs[: opts["limit"]]

        total = qs.count()
        if not total:
            self.stdout.write("Nothing to backfill — every row already has copy.")
            return

        self.stdout.write(f"Will process {total} exercises…")
        if opts["dry_run"]:
            self.stdout.write(self.style.WARNING("DRY RUN — no DB writes."))

        written = 0
        failed = 0
        for i, ex in enumerate(qs, start=1):
            try:
                copy = self._generate(ex, api_key)
            except Exception as e:  # noqa: BLE001 — log + continue
                failed += 1
                self.stdout.write(self.style.ERROR(
                    f"  [{i}/{total}] FAIL {ex.name}: {e}"
                ))
                # Don't hammer on errors — back off a beat.
                time.sleep(2)
                continue

            self.stdout.write(f"  [{i}/{total}] {ex.name} — {copy}")
            if not opts["dry_run"]:
                ex.primary_benefit = copy
                ex.save(update_fields=["primary_benefit"])
                written += 1
            time.sleep(opts["sleep"])

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Wrote {written}, failed {failed}, total {total}."
        ))

    def _generate(self, ex: ExerciseCatalog, api_key: str) -> str:
        # Pack the relevant context. Equipment + muscle group + level
        # are the most useful signal for Claude; we don't send the
        # instructions field because the model already knows how
        # major lifts work, and including it bloats input cost.
        user_message = (
            f"Exercise: {ex.name}\n"
            f"Primary muscle: {ex.muscle_group or 'unspecified'}\n"
            f"Equipment: {ex.equipment or 'unspecified'}\n"
            f"Level: {ex.level or 'unspecified'}\n"
            f"Mechanic: {ex.mechanic or 'unspecified'}\n"
            f"Category: {ex.category or 'unspecified'}\n"
        )

        body = {
            "model":      ANTHROPIC_MODEL,
            "max_tokens": 220,
            "system":     SYSTEM_PROMPT,
            "messages":   [{"role": "user", "content": user_message}],
        }
        resp = requests.post(
            ANTHROPIC_URL,
            json=body,
            headers={
                "x-api-key":         api_key,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            timeout=30.0,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"anthropic {resp.status_code}: {resp.text[:200]}"
            )

        data = resp.json()
        # Content is a list of blocks; we asked for plain text so
        # the first block.text holds our JSON string.
        raw = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                raw += block.get("text", "")
        raw = raw.strip()

        # The system prompt says "JSON only" but be defensive — strip
        # code fences if Claude adds them despite instructions.
        if raw.startswith("```"):
            raw = raw.strip("`").lstrip("json").strip()

        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"non-JSON response: {raw[:120]}…"
            ) from e

        copy = (obj.get("primary_benefit") or "").strip()
        if not copy:
            raise RuntimeError("empty primary_benefit in response")
        return copy
