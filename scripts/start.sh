#!/usr/bin/env bash
# Clear Sky — start the service (Linux / macOS / WSL).
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -c "import cryptography" 2>/dev/null || pip install --quiet cryptography || echo "note: push notifications disabled (cryptography not installed)"
exec python3 app/server.py "$@"
