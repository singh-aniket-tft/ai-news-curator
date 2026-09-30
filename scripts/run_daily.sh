#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
if [[ -f .env ]]; then
  set -a
  # .env is local and gitignored; use shell KEY=value syntax.
  source .env
  set +a
fi

if [[ -z "${GEMINI_API_KEY:-${GOOGLE_API_KEY:-}}" ]]; then
  echo "No Gemini API key configured for the daily run. Run scripts/configure_daily_key.sh first." >&2
  exit 2
fi

exec .venv/bin/news-curator run \
  --max-items "${MAX_ITEMS_PER_TOPIC:-3}" \
  --max-total-items "${MAX_TOTAL_ITEMS_PER_DAY:-20}"
