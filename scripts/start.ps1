# Start the Electron UI; it spawns the Python backend from backend\.venv with a random per-launch token.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$env:GIDEON_SPAWN_BACKEND = "1"
if (-not $env:GIDEON_PYTHON) { $env:GIDEON_PYTHON = Join-Path $Root "backend\.venv\Scripts\python.exe" }
$env:GIDEON_BACKEND_DIR = Join-Path $Root "backend"
if (-not $env:GIDEON_AUDIO) { $env:GIDEON_AUDIO = "mic" }
Push-Location (Join-Path $Root "ui")
try { npx electron . } finally { Pop-Location }
