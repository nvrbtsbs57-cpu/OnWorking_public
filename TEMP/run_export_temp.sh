#!/usr/bin/env bash
set -euo pipefail

cd "$HOME/BOT_GODMODE_PUBLIC"

./TEMP/quick_export_temp.sh "TEMP: export M10"

echo
echo "[FINI] (Ferme la fenêtre ou appuie sur Entrée)"
read -r
