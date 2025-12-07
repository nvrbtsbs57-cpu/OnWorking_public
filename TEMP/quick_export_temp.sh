#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$HOME/BOT_GODMODE_PUBLIC"
cd "$REPO_ROOT"

# Message de commit (paramètre optionnel)
MSG="${1:-"TEMP: update exports"}"

echo "[INFO] Scan de TEMP/ et ajout des fichiers au stage..."
git add TEMP

# Vérifier s'il y a vraiment quelque chose de nouveau/modifié
if git diff --cached --quiet; then
  echo "[INFO] Aucun changement dans TEMP (tout est déjà importé dans git)."
else
  echo "[INFO] Changements détectés dans TEMP → commit + push..."
  git commit -m "$MSG"
  git push origin main
fi

echo
echo "---------------------------------------------------"

gen_links() {
  # Liste tous les fichiers sous TEMP (récursif)
  find TEMP -type f | sort | while read -r f; do
    rel="${f#TEMP/}"  # ex: PLAN_TRAVAIL.md ou PROJECT_SNAPSHOT_M10/data/x.json
    echo "TEMP/$rel"
    echo "  RAW  : https://raw.githubusercontent.com/nvrbtsbs57-cpu/OnWorking_public/refs/heads/main/TEMP/$rel"
    echo "  BLOB : https://github.com/nvrbtsbs57-cpu/OnWorking_public/blob/main/TEMP/$rel"
  done
}

# Afficher les liens dans le terminal
gen_links

echo "---------------------------------------------------"

# Sauvegarder aussi les liens dans un fichier
gen_links > TEMP/LINKS_LAST_SYNC

echo "[INFO] Liens enregistrés dans TEMP/LINKS_LAST_SYNC"
echo "---------------------------------------------------"

