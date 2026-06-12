#!/usr/bin/env bash
# CC re-deploy script (Story 1.7) — idempotent, run on the VPS for every
# subsequent deploy:  sudo bash /srv/cc/deploy/deploy.sh
# First-time install is deploy/README.md's runbook.
#
# Runs as root; repo operations drop to the service user `cc` so the
# working tree never accumulates root-owned files (root-owned .venv/,
# node_modules/ or .next/ would break the User=cc services and every
# later sudo -u cc operation).
#
# Steps (order matters: migrate BEFORE restart, build BEFORE restart):
#   git pull → pip install → alembic upgrade head → npm ci + build →
#   refresh systemd units → restart
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "must run as root (systemctl + sudo -u cc):  sudo bash /srv/cc/deploy/deploy.sh" >&2
    exit 1
fi

cd /srv/cc

# Production deploys track main only — a story branch left checked out
# (e.g. right after a first install from a branch) must be switched first.
branch=$(sudo -u cc git rev-parse --abbrev-ref HEAD)
if [[ "$branch" != "main" ]]; then
    echo "checkout is on '$branch', not main — fix with:  sudo -u cc git switch main" >&2
    exit 1
fi

echo "==> [1/6] git pull (--ff-only: a diverged checkout fails loudly)"
sudo -u cc git pull --ff-only

echo "==> [2/6] backend deps"
sudo -u cc backend/.venv/bin/pip install -e ./backend

echo "==> [3/6] database migrations"
(cd backend && sudo -u cc .venv/bin/alembic upgrade head)

echo "==> [4/6] frontend build"
(cd frontend && sudo -u cc npm ci && sudo -u cc npm run build)

echo "==> [5/6] refresh systemd units"
# cc-backup.{service,timer} (Story 4.4) are refreshed too; copying them does
# not enable the timer — first-time enable is README step 12.
cp deploy/cc-core.service deploy/cc-web.service \
   deploy/cc-backup.service deploy/cc-backup.timer /etc/systemd/system/
systemctl daemon-reload

echo "==> [6/6] restart services"
systemctl restart cc-core cc-web

echo "==> deploy done"
