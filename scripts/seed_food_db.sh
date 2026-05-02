#!/bin/bash
# FOOD-DB-SEED — one-shot bootstrap script for the curated food
# catalogue. Run from Render shell (or a local clone with the
# Render env mirrored). Idempotent — safe to re-run.
#
# What it does:
#   1. Runs migrations (nutrition 0009 + 0010).
#   2. Downloads USDA SR Legacy + Foundation JSON bundles into /tmp.
#   3. Imports each via `manage.py import_curated_foods --source=usda`.
#   4. Runs cross-source dedup.
#   5. Reports row counts at the end.
#
# Why bash + not a management command:
#   • Network downloads belong in shell, not Django.
#   • Lets you re-run individual steps by editing locally.
#   • Render shell expects bash anyway.
#
# Usage from Render shell:
#   bash scripts/seed_food_db.sh
#
# Sources:
#   - USDA SR Legacy Food JSON:
#       https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_sr_legacy_food_json_2018-04.zip
#     ~7,700 reference foods, public domain.
#   - USDA Foundation Food JSON (more recent, hand-curated):
#       https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_foundation_food_json_2024-04-18.zip
#     ~400 reference foods, public domain.

set -euo pipefail

WORKDIR=/tmp/marrow_food_seed
mkdir -p "$WORKDIR"
cd "$WORKDIR"

echo "── Step 1 — migrations ─────────────────────────────────────"
cd /opt/render/project/src 2>/dev/null || cd "$(dirname "$0")/.."
python manage.py migrate nutrition

echo ""
echo "── Step 2 — USDA SR Legacy download ────────────────────────"
SR_URL="https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_sr_legacy_food_json_2018-04.zip"
SR_ZIP="$WORKDIR/sr_legacy.zip"
if [ ! -f "$SR_ZIP" ]; then
    echo "Downloading SR Legacy bundle (~25MB)…"
    curl -fL "$SR_URL" -o "$SR_ZIP"
else
    echo "SR Legacy bundle already cached at $SR_ZIP"
fi

cd "$WORKDIR"
unzip -o "$SR_ZIP" >/dev/null
SR_JSON=$(find "$WORKDIR" -name "*sr_legacy*.json" -type f | head -1)
if [ -z "$SR_JSON" ]; then
    echo "ERROR: couldn't find SR Legacy JSON inside the unzipped bundle."
    exit 1
fi
echo "SR Legacy JSON: $SR_JSON"

echo ""
echo "── Step 3 — USDA Foundation download ───────────────────────"
FF_URL="https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_foundation_food_json_2024-04-18.zip"
FF_ZIP="$WORKDIR/foundation.zip"
if [ ! -f "$FF_ZIP" ]; then
    echo "Downloading Foundation bundle (~5MB)…"
    curl -fL "$FF_URL" -o "$FF_ZIP"
else
    echo "Foundation bundle already cached at $FF_ZIP"
fi

unzip -o "$FF_ZIP" >/dev/null
FF_JSON=$(find "$WORKDIR" -name "*foundation*.json" -type f | head -1)
if [ -z "$FF_JSON" ]; then
    echo "ERROR: couldn't find Foundation JSON inside the unzipped bundle."
    exit 1
fi
echo "Foundation JSON: $FF_JSON"

echo ""
echo "── Step 4 — import SR Legacy ──────────────────────────────"
cd /opt/render/project/src 2>/dev/null || cd "$(dirname "$0")/.."
python manage.py import_curated_foods --source=usda --path="$SR_JSON"

echo ""
echo "── Step 5 — import Foundation ─────────────────────────────"
python manage.py import_curated_foods --source=usda --path="$FF_JSON"

echo ""
echo "── Step 6 — dedup pass ────────────────────────────────────"
python manage.py dedup_curated_foods

echo ""
echo "── Done. Catalogue size: ──────────────────────────────────"
python manage.py shell -c "
from apps.nutrition.models import CuratedFood
total = CuratedFood.objects.count()
active = CuratedFood.objects.exclude(tags__contains='duplicate_of:').count()
print(f'  total rows: {total}')
print(f'  active (non-duplicate): {active}')
print(f'  by source:')
from django.db.models import Count
for row in CuratedFood.objects.values('source').annotate(n=Count('id')).order_by('-n'):
    print(f'    {row[\"source\"]:10s}  {row[\"n\"]}')
"
