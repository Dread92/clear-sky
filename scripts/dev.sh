#!/usr/bin/env bash
# Clear Sky — demo mode: fake alerts, no API token needed. http://localhost:8642/m
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 app/server.py --demo "$@"
