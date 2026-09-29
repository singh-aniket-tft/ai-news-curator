#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
if [[ -f .env ]]; then
  set -a
  # .env is local and gitignored; use shell KEY=value syntax.
  source .env
  set +a
fi

exec .venv/bin/news-curator run --max-items "${MAX_ITEMS_PER_TOPIC:-20}"
