#!/usr/bin/env bash

# --------- CONFIG SIMPLE ---------
PROJECT_ROOT="/home/dd/BOT_GODMODE/BOT_GODMODE"
PYTHON="/home/dd/BOT_GODMODE/venv/bin/python"
LOG_DIR="$PROJECT_ROOT/logs"
# --------------------------------

echo "== BOT GODMODE – START ALL =="

# Vérif venv
if [ ! -x "$PYTHON" ]; then
  echo "ERREUR : Python du venv introuvable : $PYTHON"
  echo "Vérifie que le venv existe bien, sinon recrée-le."
  read -p "Appuie sur Entrée pour fermer..."
  exit 1
fi

mkdir -p "$LOG_DIR"
cd "$PROJECT_ROOT"

echo "Dossier projet : $PROJECT_ROOT"
echo "Logs : $LOG_DIR"
echo

# ---------- DASHBOARD ----------
if pgrep -f "scripts/start_bot.py" > /dev/null; then
  echo "[Dashboard] déjà en cours."
else
  echo "[Dashboard] lancement..."
  nohup "$PYTHON" scripts/start_bot.py >> "$LOG_DIR/dashboard.log" 2>&1 &
  sleep 1
fi

# ---------- RUNTIME MEMECOIN ----------
if pgrep -f "scripts/run_memecoin_runtime.py" > /dev/null; then
  echo "[Runtime] déjà en cours."
else
  echo "[Runtime] lancement..."
  nohup "$PYTHON" scripts/run_memecoin_runtime.py >> "$LOG_DIR/runtime.log" 2>&1 &
  sleep 1
fi

echo
echo "Tout est lancé (ou déjà en cours)."
echo "  - Dashboard : http://127.0.0.1:8001/godmode/ui/"
echo "  - Logs      :"
echo "        $LOG_DIR/dashboard.log"
echo "        $LOG_DIR/runtime.log"

# Ouvre le dashboard automatiquement si possible
if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "http://127.0.0.1:8001/godmode/ui/" >/dev/null 2>&1 &
fi

echo
read -p "Appuie sur Entrée pour fermer cette fenêtre..."
