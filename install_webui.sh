#!/bin/bash
set -e

# Wrapper: install Web UI extra dependencies for your OS.
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

case "$(uname -s | tr '[:upper:]' '[:lower:]')" in
  linux*)
    exec ./install_webui_linux.sh
    ;;
  darwin*)
    exec ./install_webui_macos.sh
    ;;
  *)
    echo "Unsupported OS for this script."
    echo "Run:"
    echo "  ./install_webui_linux.sh  (Linux)"
    echo "  ./install_webui_macos.sh  (macOS)"
    exit 1
    ;;
esac

