# Set up the PoB2 headless fork for this MCP server (Windows / PowerShell).
#
# The fork (~750 MB, Path of Building Community, MIT) is NOT vendored in this
# repo. This script clones it at the exact pinned commit the engine was built
# and tested against, then injects our headless entrypoint (engine/mcp_entry.lua)
# into it. Re-running is safe (idempotent).
$ErrorActionPreference = "Stop"

$RepoUrl      = "https://github.com/PathOfBuildingCommunity/PathOfBuilding-PoE2.git"
$PinnedCommit = "ce8bffaba31f8e68cfce70579e1c96465e7c133c"
$DockerImage  = "ghcr.io/pathofbuildingcommunity/pathofbuilding-tests:latest"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Fork = Join-Path $Root "fork"

foreach ($tool in @("git", "docker", "python")) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        throw "ERROR: '$tool' not found in PATH"
    }
}

if (-not (Test-Path (Join-Path $Fork ".git"))) {
    Write-Host ">> Cloning PoB2 fork (partial clone, blobs on demand)..."
    git clone --filter=blob:none $RepoUrl $Fork
}

Write-Host ">> Checking out pinned commit $PinnedCommit ..."
try   { git -C $Fork fetch --filter=blob:none origin $PinnedCommit }
catch { git -C $Fork fetch origin }
git -C $Fork checkout --quiet $PinnedCommit

Write-Host ">> Injecting engine/mcp_entry.lua ..."
Copy-Item (Join-Path $Root "engine\mcp_entry.lua") (Join-Path $Fork "src\mcp_entry.lua") -Force

Write-Host ">> Pulling the headless Docker image ..."
docker pull $DockerImage

# Install Python deps into a dedicated virtualenv, so we don't depend on the
# system Python having a working pip.
$Py = Get-Command python3 -ErrorAction SilentlyContinue
if (-not $Py) { $Py = Get-Command python -ErrorAction SilentlyContinue }
if (-not $Py) { throw "python not found in PATH" }

$VenvDir = Join-Path $Root ".venv"
if (-not (Test-Path $VenvDir)) {
    Write-Host ">> Creating virtualenv (.venv) ..."
    & $Py.Source -m venv $VenvDir
}

$VenvPy = Join-Path $VenvDir "Scripts\python.exe"
Write-Host ">> Installing Python deps into .venv ..."
& $VenvPy -m pip install --upgrade pip | Out-Null
& $VenvPy -m pip install -r (Join-Path $Root "server\requirements.txt")

Write-Host ""
Write-Host "Done. Point your MCP client at the venv Python:"
Write-Host "    $VenvPy -m pob_mcp.server   (PYTHONPATH=$Root\server)"
Write-Host "See README / .mcp.json.example. Then restart the client."
