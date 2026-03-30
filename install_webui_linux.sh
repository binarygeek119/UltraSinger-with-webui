#!/bin/bash
set -e

# Install Web UI extra dependencies into the repo-local .venv
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

export UV_LINK_MODE=copy

# Install uv if missing
if ! command -v uv &> /dev/null; then
  echo "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

if ! command -v uv &> /dev/null; then
  echo "Error: uv could not be found or installed"
  echo "Please ensure your shell PATH includes ~/.local/bin"
  exit 1
fi

echo "uv version:"
uv --version

# Create/sync base environment if .venv doesn't exist yet.
if [ ! -d ".venv" ]; then
  echo "No .venv found; syncing base dependencies for Linux..."
  uv sync --extra linux
fi

echo "Installing UltraSinger + WebUI packages (optional extra: webui)..."
uv pip install -e ".[webui]"

echo "Updating yt-dlp to latest..."
uv pip install -U yt-dlp

echo "Verifying uvicorn..."
".venv/bin/python" -c "import uvicorn; print('uvicorn OK:', uvicorn.__version__)"

echo "Done. Start with:"
echo '  PYTHONPATH="$(pwd)" .venv/bin/python -m webui'

