#!/bin/bash
# Layerforge self-test for macOS - double-click this file. Shows PASS or FAIL.
cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  echo "First start: installing uv (one time only)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh || { echo "Could not install uv. Check your internet connection."; read -r -p "Press Enter to close"; exit 1; }
  export PATH="$HOME/.local/bin:$PATH"
fi
echo "Preparing Layerforge (first start can take a minute)..."
uv sync --quiet || { echo "FAIL - setup failed (see above)."; read -r -n 1 -s -p "Press any key to close"; exit 1; }
uv run layerforge selftest
echo
read -r -n 1 -s -p "Press any key to close this window."
echo
