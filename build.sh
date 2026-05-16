#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate
# SOLO-02 — refresh the public programmes catalog from the seed file.
# Idempotent; cheap to run on every deploy.
python manage.py seed_solo_programmes
# YUHONAS-IMAGE-SYNC (May 2026, Deen QC) — import the public Free
# Exercise DB (yuhonas/free-exercise-db) so every catalog row has
# `image_url` pointing at a CDN-served start-frame image. iOS
# `ExerciseAnimationView` reads this directly; without it every
# thumbnail falls back to an SF symbol. The command is idempotent
# (update_or_create on (source, external_id)), so re-running on
# every deploy keeps the catalog fresh without cost.
python manage.py import_exercise_catalog \
    --source=free_exercise_db \
    --path=apps/workouts/management/commands/data/free_exercise_db.json

# FORM-COPY-SEED (May 2026, Deen QC) — load the hand-written form
# copy YAMLs into the catalog. The command globs
# apps/workouts/seed/form_copy/*.yaml by default and pulls in:
#   • deen_priority_30.yaml — 30 priority lifts on Deen's PT plan
#   • staples_push/pull/legs/core.yaml — staple movements per pattern
#   • staples_extended.yaml — 44 common variations
#   • bulk_generated.yaml — 818 entries covering every remaining
#     yuhonas catalog row (full coverage to 100%)
# Hand-written + voice-matched static content. No runtime AI.
# Idempotent: rows already fully populated stay untouched; partial
# rows get topped up. Re-run with --overwrite to force a re-load
# after a voice revision.
python manage.py seed_exercise_form_copy
# APPLE-REVIEW-BYPASS — provision (or refresh) the reviewer-only test
# account. Idempotent; ensures reviewer@gymflow.coach exists on Pro AI
# tier so the magic-link bypass route can sign them in. Set the env
# var APPLE_REVIEW_TOKEN to a secret value to actually open the
# bypass route; without it the route stays closed.
python manage.py seed_reviewer_account