#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CRON_TMP="$(mktemp)"
trap 'rm -f "$CRON_TMP"' EXIT

mkdir -p "$BACKEND_DIR/logs"
crontab -l 2>/dev/null | awk \
  '!/techpilot-hn-ingest/ && $0 != "PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"' \
  > "$CRON_TMP" || true
printf '%s\n' \
  '# BEGIN techpilot-hn-ingest' \
  'PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin' \
  "0 2 * * 1-6 /bin/bash $SCRIPT_DIR/cron_job.sh daily >> $BACKEND_DIR/logs/daily.log 2>&1 # techpilot-hn-ingest daily" \
  "0 2 * * 0 /bin/bash $SCRIPT_DIR/cron_job.sh weekly >> $BACKEND_DIR/logs/weekly.log 2>&1 # techpilot-hn-ingest weekly" \
  '# END techpilot-hn-ingest' >> "$CRON_TMP"

crontab "$CRON_TMP"
echo "Installed HN ingestion cron jobs (2:00 AM local time)."
