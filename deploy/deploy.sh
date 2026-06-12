#!/usr/bin/env bash
# CC re-deploy script (Story 1.7) — idempotent, run on the VPS for every
# subsequent deploy. First-time install is deploy/README.md's runbook.
#
# Steps (order matters: migrate BEFORE restart, build BEFORE restart):
#   git pull → pip install → alembic upgrade head → npm ci + build → restart
set -euo pipefail

cd /srv/cc

echo "==> [1/5] git pull (--ff-only: a diverged checkout fails loudly)"
git pull --ff-only

echo "==> [2/5] backend deps"
backend/.venv/bin/pip install -e ./backend

echo "==> [3/5] database migrations"
(cd backend && .venv/bin/alembic upgrade head)

echo "==> [4/5] frontend build"
(cd frontend && npm ci && npm run build)

echo "==> [5/5] restart services"
sudo systemctl restart cc-core cc-web

echo "==> deploy done"
