#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
if [[ -e .env ]]; then
  echo ".env already exists. Edit it locally rather than overwriting it."
  exit 1
fi

read -r -s -p "Enter a new Gemini API key for daily runs: " key
printf '\n'
if [[ -z "$key" ]]; then
  echo "No key entered."
  exit 1
fi

umask 077
printf 'GEMINI_API_KEY=%q\n' "$key" > .env
unset key
chmod 600 .env
echo "Saved the key in the gitignored .env file with owner-only permissions."
