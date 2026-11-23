#!/usr/bin/env bash
# otui in a GitHub codespace: set up once, in the prebuild when the repository has one.
#
# Installs uv, otui's Python packages, the browser app, TIMES.jl (the pinned commit) and the open
# models, then reads demo12 once so the first click is quick. Everything goes into the codespace,
# a cloud computer under the user's GitHub account; nothing touches the user's own computer.
#
# --if-needed: only what is missing. serve.sh runs it at each start, because rebuilding a
# codespace's container empties its home folder, where Julia's packages live.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$HOME/.juliaup/bin:$PATH"

if_needed=false
[[ "${1:-}" == "--if-needed" ]] && if_needed=true

# uv, from its official installer (as in the README's Quickstart for Linux)
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync

# the browser app (Node.js is only needed to build it)
[[ -f src/otui/web/index.html ]] || (cd frontend && npm ci && npm run build)

set_up() {
  uv run python -c "import sys; from otui.bridge import is_set_up; sys.exit(not is_set_up('pinned'))"
}
if $if_needed && set_up; then
  exit 0
fi

# TIMES.jl at the commit otui is tested on (OTUI_TIMESJL=pinned in devcontainer.json): a codespace
# never builds a newer TIMES.jl in the background on its owner's Codespaces allowance
uv run otui julia-setup --channel pinned

# the open models behind the Try buttons (OTNYC, OTCGEN, OTUS9r), hash-checked, so they open
# without a download; then demo12 once, which also fetches TIMES_Demo through TIMES.jl
uv run python -c "from otui import openmodels; [openmodels.fetch(k) for k in openmodels.MODELS]"
uv run otui bench --repeat 1
