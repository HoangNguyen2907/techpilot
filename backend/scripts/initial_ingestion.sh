#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$BACKEND_DIR/.." && pwd)"

# Download dictionary-matched HN stories from 2025 onward, then load and tag them.
cd "$REPO_ROOT"
uv run data/download.py --from-dict --since 2026-06-01 --min-points 500

cd "$BACKEND_DIR"
uv run python -m app.ingest.run_pipeline --load --tag
