#!/usr/bin/env bash
set -e

ROOT_DIR="$HOME/BOT_GODMODE"
APP_DIR="$ROOT_DIR/BOT_GODMODE"
VENV_DIR="$ROOT_DIR/venv"
DASHBOARD_URL="http://127.0.0.1:8001/godmode/ui"

echo "ROOT_DIR = $ROOT_DIR"
echo "APP_DIR  = $APP_DIR"
echo "VENV_DIR = $VENV_DIR"

cd "$APP_DIR"

# --------- Activation du venv ---------
if [ -f "$VENV_DIR/bin/activate" ]; then
  echo "▶ Activation du venv..."
  # shellcheck source=/dev/null
  source "$VENV_DIR/bin/activate"
  PYTHON_CMD="python"
else
  echo "⚠️ Venv introuvable à $VENV_DIR, utilisation de python3 système."
  PYTHON_CMD="python3"
fi

echo "▶ Démarrage du bot GODMODE (scripts/start_bot.py)..."

# Ouverture Firefox après quelques secondes
(
  sleep 4
  echo "▶ Ouverture de Firefox sur $DASHBOARD_URL"
  firefox "$DASHBOARD_URL" >/dev/null 2>&1 &
) &

$PYTHON_CMD scripts/start_bot.py

