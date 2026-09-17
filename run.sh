#!/bin/bash
# Täglicher Lauf, wird von launchd (de.privat.hausaufgaben.plist) aufgerufen.
# Zugangsdaten und Gruppen-JID stehen in .env (SM_USER, SM_PASSWORD, WA_GROUP_JID).
set -euo pipefail

cd "$(dirname "$0")"

# launchd startet mit minimalem PATH; uv liegt bei Homebrew oder ~/.cargo/bin.
export PATH="/opt/homebrew/bin:$HOME/.cargo/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"

if [ ! -f .env ]; then
    echo "Keine .env gefunden in $(pwd)" >&2
    exit 2
fi

echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="
exec uv run --env-file .env hausaufgaben "$@"
