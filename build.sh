#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate
# SOLO-02 — refresh the public programmes catalog from the seed file.
# Idempotent; cheap to run on every deploy.
python manage.py seed_solo_programmes