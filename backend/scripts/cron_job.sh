#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOCK_DIR="$BACKEND_DIR/.hn_ingestion.lock"

case "${1:-}" in
  daily) JOB="$SCRIPT_DIR/daily_fetch.sh" ;;
  weekly) JOB="$SCRIPT_DIR/weekly_refresh.sh" ;;
  *) echo "Usage: $0 {daily|weekly}" >&2; exit 2 ;;
esac

mkdir -p "$BACKEND_DIR/logs"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "HN ingestion skipped: another scheduled run holds $LOCK_DIR"
  exit 0
fi
trap 'rmdir "$LOCK_DIR"' EXIT HUP INT TERM

"$JOB"
