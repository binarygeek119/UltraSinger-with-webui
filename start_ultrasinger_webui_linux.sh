#!/bin/bash
set -e

# Activate venv and run UltraSinger WebUI (tray mode is controlled by cfg.tray_enabled)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -d ".venv" ]; then
  echo "Error: No virtual environment found at .venv"
  echo "Please run:"
  echo "  ./install_webui_linux.sh"
  exit 1
fi

source .venv/bin/activate

export PYTHONPATH="$SCRIPT_DIR"
echo "Starting UltraSinger WebUI..."
python -m webui

