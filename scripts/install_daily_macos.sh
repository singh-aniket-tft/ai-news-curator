#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "$0")/.." && pwd)"
agent_dir="$HOME/Library/LaunchAgents"
agent_path="$agent_dir/com.aniket.ai-news-curator.plist"
agent_label="com.aniket.ai-news-curator"

if [[ ! -x "$project_root/.venv/bin/news-curator" ]]; then
  echo "Install the project in .venv before scheduling it. See README.md." >&2
  exit 1
fi

mkdir -p "$agent_dir" "$project_root/logs"
PROJECT_ROOT="$project_root" AGENT_PATH="$agent_path" "$project_root/.venv/bin/python" - <<'PY'
import os
import plistlib
from pathlib import Path

root = Path(os.environ["PROJECT_ROOT"])
path = Path(os.environ["AGENT_PATH"])
agent = {
    "Label": "com.aniket.ai-news-curator",
    "ProgramArguments": ["/bin/bash", str(root / "scripts" / "run_daily.sh")],
    "WorkingDirectory": str(root),
    "StartCalendarInterval": {"Hour": 8, "Minute": 0},
    "StandardOutPath": str(root / "logs" / "daily.stdout.log"),
    "StandardErrorPath": str(root / "logs" / "daily.stderr.log"),
}
path.write_bytes(plistlib.dumps(agent))
PY

chmod 644 "$agent_path"
plutil -lint "$agent_path"
launchctl bootout "gui/$(id -u)" "$agent_path" >/dev/null 2>&1 || true
if ! launchctl bootstrap "gui/$(id -u)" "$agent_path"; then
  echo "Could not register the LaunchAgent. Run this installer from your normal macOS Terminal." >&2
  exit 1
fi
launchctl print "gui/$(id -u)/$agent_label" >/dev/null
echo "Installed $agent_label for 08:00 local time each day."
echo "Logs: $project_root/logs/daily.stdout.log and daily.stderr.log"
