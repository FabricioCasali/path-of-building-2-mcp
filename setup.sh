#!/usr/bin/env bash
# Set up the PoB2 headless fork for this MCP server.
#
# The fork (~750 MB, Path of Building Community, MIT) is NOT vendored in this
# repo. This script clones it at the exact pinned commit the engine was built
# and tested against, then injects our headless entrypoint (engine/mcp_entry.lua)
# into it. Re-running is safe (idempotent).
set -euo pipefail

REPO_URL="https://github.com/PathOfBuildingCommunity/PathOfBuilding-PoE2.git"
PINNED_COMMIT="ce8bffaba31f8e68cfce70579e1c96465e7c133c"
DOCKER_IMAGE="ghcr.io/pathofbuildingcommunity/pathofbuilding-tests:latest"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FORK="$ROOT/fork"

command -v git    >/dev/null || { echo "ERROR: git not found";    exit 1; }
command -v docker >/dev/null || { echo "ERROR: docker not found"; exit 1; }

if [ ! -d "$FORK/.git" ]; then
  echo ">> Cloning PoB2 fork (partial clone, blobs on demand)..."
  git clone --filter=blob:none "$REPO_URL" "$FORK"
fi

echo ">> Checking out pinned commit $PINNED_COMMIT ..."
git -C "$FORK" fetch --filter=blob:none origin "$PINNED_COMMIT" 2>/dev/null || git -C "$FORK" fetch origin
git -C "$FORK" checkout --quiet "$PINNED_COMMIT"

echo ">> Injecting engine/mcp_entry.lua ..."
cp "$ROOT/engine/mcp_entry.lua" "$FORK/src/mcp_entry.lua"

echo ">> Pulling the headless Docker image ..."
docker pull "$DOCKER_IMAGE"

# Install Python deps into a dedicated virtualenv. This sidesteps two common
# breakages on modern Linux: a system `python` without pip ("No module named
# pip") and PEP 668 "externally-managed-environment" errors. venv brings its
# own pip via ensurepip.
PY="$(command -v python3 || command -v python || true)"
[ -n "$PY" ] || { echo "ERROR: python3 not found in PATH"; exit 1; }

if [ ! -d "$ROOT/.venv" ]; then
  echo ">> Creating virtualenv (.venv) ..."
  "$PY" -m venv "$ROOT/.venv" || {
    echo "ERROR: could not create the virtualenv."
    echo "       On Debian/Ubuntu install it first:  sudo apt install python3-venv"
    exit 1
  }
fi

VENV_PY="$ROOT/.venv/bin/python"
echo ">> Installing Python deps into .venv ..."
"$VENV_PY" -m pip install --upgrade pip >/dev/null
"$VENV_PY" -m pip install -r "$ROOT/server/requirements.txt"

echo
echo "Done. Point your MCP client at the venv Python:"
echo "    $VENV_PY -m pob_mcp.server   (PYTHONPATH=$ROOT/server)"
echo "See README / .mcp.json.example. Then restart the client."
