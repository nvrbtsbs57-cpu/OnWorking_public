#!/usr/bin/env bash

# ===== BOT_GODMODE – FINANCE JOBS (VM LINUX) =====

PROJECT_ROOT="/home/dd/BOT_GODMODE/BOT_GODMODE"
VENV_ACTIVATE="/home/dd/BOT_GODMODE/venv/bin/activate"

cd "$PROJECT_ROOT" || exit 1

if [ ! -f "$VENV_ACTIVATE" ]; then
  echo "ERREUR : venv introuvable : $VENV_ACTIVATE"
  read -p "Appuie sur Entrée pour fermer..." dummy
  exit 1
fi

# Active le venv
# shellcheck source=/dev/null
source "$VENV_ACTIVATE"

# Lance le job finance
python scripts/run_finance_jobs.py

echo
read -p "Job terminé. Appuie sur Entrée pour fermer..." dummy

