#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
if [[ ! -f .env ]]; then
  scripts/configure_daily_key.sh
fi
scripts/install_daily_macos.sh
