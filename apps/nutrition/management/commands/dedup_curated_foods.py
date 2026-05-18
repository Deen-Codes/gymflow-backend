"""
FOOD-DB-DEDUP — cross-source duplicate detection and merging.

When we add a second source (FSA on top of USDA, etc.), we'll
have multiple rows describing the same food: USDA's "Chicken,
breast, raw" and FSA's "Chicken breast, raw, meat only". Same
food, different rows.

This command finds those clusters by:
1. Normalising each row's name (lowercase, strip parenthetical
   modifiers, strip leading category prefixes like "Chicken, ").
2. Bucketing by normalised name.
3. Within each bucket, comparing macros — rows with macros
   within a 10% relative tolerance are flagged as duplicates.

Strategy chosen for resolving:
  • If multiple sources cover the same food, KEEP the highest-
    priority source (priority: afletics > usda > fsa_uk > ausnut > ciqual).
  • Lower-priority dupes get a `tags` suffix `,duplicate_of:<id>`
    and are excluded from search by default. We don't delete —
    keeps the audit trail and lets us un-merge later.

Usage:
    python manage.py dedup_curated_foods --dry-run
    python manage.py dedup_curated_foods                 # writes
    python manage.py dedup_curated_foods --tolerance 0.05 # tighter

This is idempotent — running twice does nothing on the second
run because already-flagged rows are skipped.
"""
from __future__ import annotations

import re

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.nutrition.models import CuratedFood


SOURCE_PRIORITY = {
    CuratedFood.SOURCE_AFLETICS: 5,
    CuratedFood.SOURCE_USDA:   4,
    CuratedFood.SOURCE_FSA_UK: 3,
    CuratedFood.SOURCE_AUSNUT: 2,
    CuratedFood.SOURCE_CIQUAL: 1,
}


# Strip USDA-style category prefixes ("Chicken, breast, raw" → "breast raw").
# Strip parenthetical content. Strip punctuation. Lowercase. Collapse
# whitespace. The result is good for bucketing similar names.
_PUNCT_RE   = re.compile(r"[(),.;:'\"!?]")
_SPACES_RE  = re.compile(r"\s+")
_PREFIX_RE  = re.compile(
    r"^(?:chicken|beef|pork|lamb|turkey|fish|cheese|milk|yogurt|"
    r"yoghurt|bread|pasta|rice|nuts?|seeds?)\s*[,]\s*",
    re.IGNORECASE,
)


def normalise_name(name: str) -> str:
    out = name.strip().lower()
    out = _PUNCT_RE.sub(" ", out)
    out = _PREFIX_RE.sub("", out)
    out = _SPACES_RE.sub(" ", out).strip()
    return out


def macros_close(a: CuratedFood, b: CuratedFood, tol: float) -> bool:
    """Are these two rows' macros within `tol` relative tolerance
    on every macro? Avoids merging "raw" + "cooked" variants."""
    fields = ("kcal_per_100g", "protein_per_100g", "carbs_per_100g", "fat_per_100g")
    for f in fields:
        av = getattr(a, f) or 0
        bv = getattr(b, f) or 0
        if av == 0 and bv == 0:
            continue
        # Use larger denominator to avoid div-by-zero for foods
        # with 0 protein, 0 carbs etc.
        denom = max(abs(av), abs(bv), 1.0)
        if abs(av - bv) / denom > tol:
            return False
    return True


class Command(BaseCommand):
    help = "Detect and flag cross-source duplicate CuratedFood rows."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report counts only; don't mutate.",
        )
        parser.add_argument(
            "--tolerance", type=float, default=0.10,
            help="Relative-tolerance for macro similarity (default 0.10 = 10%).",
        )

    def handle(self, *args, **opts):
        dry = opts["dry_run"]
        tol = opts["tolerance"]

        # Skip rows already flagged from a previous run.
        all_rows = list(
            CuratedFood.objects
            .exclude(tags__contains="duplicate_of:")
            .order_by("source", "name")
        )
        self.stdout.write(f"Scanning {len(all_rows)} rows…")

        # Bucket by normalised name.
        buckets: dict[str, list[CuratedFood]] = {}
        for r in all_rows:
            key = normalise_name(r.name)
            buckets.setdefault(key, []).append(r)

        merged = 0
        for key, rows in buckets.items():
            if len(rows) < 2:
                continue

            # Sort by descending source priority — first row wins.
            rows.sort(
                key=lambda r: SOURCE_PRIORITY.get(r.source, 0),
                reverse=True,
            )
            keeper = rows[0]
            for dup in rows[1:]:
                if not macros_close(keeper, dup, tol):
                    continue   # similar name but different macros (raw vs cooked)
                if "duplicate_of:" in (dup.tags or ""):
                    continue
                merged += 1
                if dry:
                    self.stdout.write(
                        f"  [{dup.source}] {dup.name!r} → "
                        f"[{keeper.source}] {keeper.name!r}"
                    )
                else:
                    new_tags = (dup.tags or "").rstrip(",")
                    if new_tags:
                        new_tags += ","
                    new_tags += f"duplicate_of:{keeper.id}"
                    dup.tags = new_tags[:200]
                    dup.save(update_fields=["tags"])

        verb = "Would flag" if dry else "Flagged"
        self.stdout.write(
            self.style.SUCCESS(f"{verb} {merged} duplicate rows.")
        )
