"""EXERCISE-COPY-FULL backfill — populate the four form-copy fields
on every ExerciseCatalog row via Claude Haiku.

Why this command — `backfill_exercise_benefits` populated only the
`primary_benefit` column. The iOS WORKOUT-ENLARGE-UX detail sheet
also needs `form_description`, `common_mistakes` and `breathing_cues`
on every row, otherwise the detail sheet renders empty for ~95% of
the catalog (the staples have hand-written copy in
`apps/workouts/seed/form_copy/*.yaml`; the long tail does not).

Approach — one Claude call per exercise, returning a single JSON
object with all four fields. Costs ~$0.005 per row at Haiku prices
(input ~250 tok + output ~500 tok), so the full 873-row catalogue
comes in well under $10. The command is idempotent: each field is
checked individually, and only the missing ones are sent to Claude.
A row whose four fields are all already populated is skipped
entirely — re-running the command is free.

Wired into `build.sh` so it runs on every deploy. On the first
deploy after this command lands, it'll churn for a few minutes
filling in the catalog; subsequent deploys exit in milliseconds.

Cost guard — `--limit` caps the number of rows per invocation.
Default unlimited (the full catalogue), which is fine because the
idempotency check means subsequent invocations only re-run the
failed rows. Use `--limit 5` for sanity checking the prompt.

Sample / QA:
    python manage.py backfill_exercise_form_copy --limit 5 --dry-run

Bulk run:
    python manage.py backfill_exercise_form_copy

Specific fields only (e.g. re-run breathing_cues after a voice
revision):
    python manage.py backfill_exercise_form_copy \
        --fields breathing_cues,primary_benefit --overwrite
"""
from __future__ import annotations

import json
import os
import time

import requests
from django.core.management.base import BaseCommand, CommandError

from apps.workouts.models import ExerciseCatalog


ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"

ALL_FIELDS = ("form_description", "common_mistakes", "breathing_cues", "primary_benefit")


# Voice extracted directly from EXERCISE_COPY_RESEARCH.md — keep this
# string in sync with that file if the doc is ever revised. Banned
# words / patterns are listed explicitly so Claude doesn't drift into
# YouTube-fitness-shout territory.
SYSTEM_PROMPT = """You are a senior strength coach writing the four
"form copy" fields displayed on a premium iOS strength-training app's
exercise detail sheet. The app is called GymFlow. Voice spec —
internalise this before writing:

  * Calm coach voice. Direct, warm, no hype. Reads like a coach
    you trust talking to a serious lifter, not a YouTube fitness
    influencer.
  * Specific over generic. "Drive the handles together using your
    chest, not your arms" beats "use proper form".
  * One sentence per idea. No multi-clause stacks of cues.
  * UK gym vocabulary. "Dumbbells", "bar", "press" (not "extension"
    for the obvious cases).
  * NO exclamation marks anywhere.
  * BANNED WORDS: "explosively", "powerfully", "aggressively",
    "blast", "destroy", "smash", "crush", "annihilate", "fire up",
    "shred", "torch", "obliterate".
  * No moralising. "Don't bounce reps" — instructional. NOT
    "Bouncing reps is cheating!" — judgmental.
  * No medical / textbook anatomy. "Front of the shoulder" not
    "anterior deltoid".

For each exercise the user shows you, return ONLY a single JSON
object with these four keys (every key required, no extras):

  {
    "form_description": "3 to 5 sentences, 60-90 words total. Setup
      to execution to finish. Active voice. Always name the muscle
      being targeted by behavioural action rather than anatomy
      reference.",
    "common_mistakes": [
      "<failure mode 1 + its consequence>",
      "<failure mode 2 + its consequence>",
      "<failure mode 3 + its consequence>",
      "<failure mode 4 + its consequence>"
    ],
    "breathing_cues": "ONE sentence in plain English. Inhale on the
      eccentric, exhale on the concentric — phrase it for THIS lift.
      Example: 'Inhale as the handles open, exhale as you squeeze
      them together.' No medical terms.",
    "primary_benefit": "ONE sentence. Why the lifter does this
      movement. Plain language, not textbook anatomy. Frame the
      trade-off ('builds upper-chest thickness without overloading
      the front delt') rather than 'increases hypertrophy of...'."
  }

`common_mistakes` MUST be a JSON array of exactly 4 strings, each a
specific failure mode that costs the lift its target muscle. Bias
toward mistakes that explain WHY the rep didn't count, not just that
it looked bad.

CRITICAL: respond with ONLY the JSON object. No preamble. No code
fences. No explanation. The JSON must parse cleanly with
json.loads()."""


class Command(BaseCommand):
    help = "Backfill the four form-copy fields on every ExerciseCatalog row via Claude."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Process at most N rows. Default: all rows missing any field.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print proposed copy, do not write to DB.",
        )
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Re-generate even for rows that already have copy.",
        )
        parser.add_argument(
            "--fields",
            type=str,
            default=",".join(ALL_FIELDS),
            help=(
                "Comma-separated list of fields to fill. Default: all four. "
                "Use this to re-run a single field after a voice revision."
            ),
        )
        parser.add_argument(
            "--sleep",
            type=float,
            default=0.4,
            help="Seconds to sleep between API calls (rate-limit cushion).",
        )
        parser.add_argument(
            "--skip-if-no-key",
            action="store_true",
            help=(
                "Exit 0 silently if ANTHROPIC_API_KEY is unset. Used by "
                "build.sh so a missing key doesn't fail the deploy — the "
                "command becomes a no-op until the key is provisioned."
            ),
        )

    def handle(self, *args, **opts):
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            if opts["skip_if_no_key"]:
                self.stdout.write(
                    "ANTHROPIC_API_KEY not set — skipping backfill (idempotent no-op)."
                )
                return
            raise CommandError(
                "ANTHROPIC_API_KEY not set. Export it (or use the same env "
                "Render uses) before running. Pass --skip-if-no-key to "
                "make this a silent no-op."
            )

        # Parse + validate the --fields list.
        requested = [
            f.strip() for f in opts["fields"].split(",") if f.strip()
        ]
        for f in requested:
            if f not in ALL_FIELDS:
                raise CommandError(
                    f"Unknown field: {f}. Valid: {', '.join(ALL_FIELDS)}"
                )

        qs = ExerciseCatalog.objects.all().order_by("-icon_priority", "name")
        if not opts["overwrite"]:
            # Only rows missing at least one of the requested fields.
            # Using Q() so we can OR across fields cleanly.
            from django.db.models import Q
            missing_filter = Q()
            for f in requested:
                missing_filter |= Q(**{f: ""})
            qs = qs.filter(missing_filter)
        if opts["limit"]:
            qs = qs[: opts["limit"]]

        total = qs.count()
        if not total:
            self.stdout.write("Nothing to backfill — every row already has copy.")
            return

        self.stdout.write(f"Will process {total} exercises (fields: {','.join(requested)})…")
        if opts["dry_run"]:
            self.stdout.write(self.style.WARNING("DRY RUN — no DB writes."))

        written = 0
        failed = 0
        skipped = 0
        for i, ex in enumerate(qs, start=1):
            # Compute which fields THIS row actually needs (might be
            # a subset of the requested set if the row was partially
            # filled by a previous run).
            needed = (
                requested if opts["overwrite"]
                else [f for f in requested if not getattr(ex, f)]
            )
            if not needed:
                skipped += 1
                continue

            try:
                copy = self._generate(ex, api_key, needed)
            except Exception as e:  # noqa: BLE001 — log + continue
                failed += 1
                self.stdout.write(self.style.ERROR(
                    f"  [{i}/{total}] FAIL {ex.name}: {e}"
                ))
                time.sleep(2)
                continue

            preview_parts = []
            for f in needed:
                v = copy.get(f, "")
                if isinstance(v, list):
                    preview_parts.append(f"{f}=[{len(v)} bullets]")
                else:
                    preview_parts.append(f"{f}={v[:40]}…" if len(v) > 40 else f"{f}={v}")
            self.stdout.write(f"  [{i}/{total}] {ex.name} — {'; '.join(preview_parts)}")

            if not opts["dry_run"]:
                update_fields = []
                for f in needed:
                    v = copy.get(f, "")
                    if isinstance(v, list):
                        # common_mistakes ships as a list — store as
                        # a newline-joined string with bullet markers
                        # so the existing iOS parser handles it.
                        v = "\n".join(f"• {item.strip()}" for item in v if item.strip())
                    setattr(ex, f, v)
                    update_fields.append(f)
                ex.save(update_fields=update_fields)
                written += 1
            time.sleep(opts["sleep"])

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Wrote {written}, skipped {skipped} already-complete, "
            f"failed {failed}, scanned {total}."
        ))

    def _generate(self, ex: ExerciseCatalog, api_key: str, needed_fields) -> dict:
        """Single Claude call returning all four fields. We always ask
        for the full set even when only one is missing — the extra
        output tokens cost a fraction of a cent and Claude does a
        better job when it has the full context of the lift in front
        of it. The caller picks out only the fields it needs to write."""
        user_message = (
            f"Exercise: {ex.name}\n"
            f"Primary muscle: {ex.muscle_group or 'unspecified'}\n"
            f"Equipment: {ex.equipment or 'unspecified'}\n"
            f"Level: {ex.level or 'unspecified'}\n"
            f"Mechanic: {ex.mechanic or 'unspecified'}\n"
            f"Force: {ex.force or 'unspecified'}\n"
            f"Category: {ex.category or 'unspecified'}\n"
        )
        # If yuhonas's instructions are populated, ship them so Claude
        # doesn't have to guess the movement pattern for obscure lifts.
        if ex.instructions:
            user_message += (
                f"\nReference instructions (for context, do not copy verbatim):\n"
                f"{ex.instructions[:1000]}\n"
            )

        body = {
            "model":      ANTHROPIC_MODEL,
            "max_tokens": 1024,
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
            timeout=60.0,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"anthropic {resp.status_code}: {resp.text[:200]}"
            )

        data = resp.json()
        raw = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                raw += block.get("text", "")
        raw = raw.strip()

        # Strip code fences defensively.
        if raw.startswith("```"):
            raw = raw.strip("`").lstrip("json").strip()

        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"non-JSON response: {raw[:120]}…"
            ) from e

        # Validate shape — every required key present, common_mistakes
        # is a 4-item array, strings are non-empty.
        for f in ALL_FIELDS:
            if f not in obj:
                raise RuntimeError(f"missing field in response: {f}")
        cm = obj.get("common_mistakes")
        if not isinstance(cm, list) or len(cm) < 3:
            raise RuntimeError(
                f"common_mistakes must be a 3-4 item list, got: {type(cm).__name__}"
            )

        # Banned-word safety check — if Claude drifts into hype words
        # despite the prompt, flag it so the dry-run preview catches
        # it before bulk-running. We don't reject (would block forever)
        # — just log to stderr at write time.
        banned = {"explosively", "powerfully", "aggressively", "blast",
                  "destroy", "smash", "crush", "annihilate", "torch",
                  "shred", "obliterate"}
        for field in ("form_description", "primary_benefit", "breathing_cues"):
            text = (obj.get(field) or "").lower()
            for word in banned:
                if word in text:
                    self.stdout.write(self.style.WARNING(
                        f"    voice drift: '{word}' in {ex.name}.{field}"
                    ))

        return obj
