#!/usr/bin/env bash
# One-time/idempotent local setup for macOS/Linux.
#
#   ./scripts/setup.sh
#
# Creates relay/.venv, installs pinned Python deps, and runs npm install.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

echo "==> Setting up Python relay (relay/.venv)"
if [ ! -d relay/.venv ]; then
  python3 -m venv relay/.venv
fi
relay/.venv/bin/python -m pip install --upgrade pip -q
relay/.venv/bin/python -m pip install -r relay/requirements.lock.txt

echo "==> Installing Node dependencies"
npm install

echo "==> Done. Copy .env.example -> .env.local and relay/.env.example -> relay/.env if you need to override defaults."
echo "==> Start both processes with: npm run dev:all"
