#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate
# SOLO-02 — refresh the public programmes catalog from the seed file.
# Idempotent; cheap to run on every deploy.
python manage.py seed_solo_programmes
# APPLE-REVIEW-BYPASS — provision (or refresh) the reviewer-only test
# account. Idempotent; ensures reviewer@gymflow.coach exists on Pro AI
# tier so the magic-link bypass route can sign them in. Set the env
# var APPLE_REVIEW_TOKEN to a secret value to actually open the
# bypass route; without it the route stays closed.
python manage.py seed_reviewer_account