#!/usr/bin/env bash
# Mirror the canonical engine into api/ so Vercel's Python function is
# self-contained (the function directory is what's guaranteed bundled).
# Run after editing simsoc/, config/, or scenarios/ — the pre-push hook
# refuses to push if the mirror is stale.
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf api/simsoc api/config api/scenarios
cp -r simsoc api/simsoc && cp -r config api/config && cp -r scenarios api/scenarios
# CLI-only modules aren't needed (or importable) inside the function:
rm -f api/simsoc/cli.py api/simsoc/__main__.py api/simsoc/report.py api/simsoc/validate.py
find api -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
echo "api/ mirror synced."
