#!/usr/bin/env bash
# Starts otui each time the codespace opens. GitHub forwards port 8642 to its owner only (a
# private port) and opens it in a new tab; if the browser blocks that, open it from the Ports tab.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$HOME/.juliaup/bin:$PATH"

if (exec 3<>/dev/tcp/127.0.0.1/8642) 2>/dev/null; then
  echo "otui is already running in this codespace: open port 8642 from the Ports tab."
  exit 0
fi
bash .devcontainer/setup.sh --if-needed
exec uv run otui serve
