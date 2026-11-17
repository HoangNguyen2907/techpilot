#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$BACKEND_DIR/.." && pwd)"

# Fetch dictionary-matched stories from 2025 onward and refresh the latest day.
cd "$REPO_ROOT"
uv run data/download.py --from-dict --since 2025 --min-points 50 --refresh-days 1

cd "$BACKEND_DIR"
uv run python -m app.ingest.run_pipeline --load --tag
