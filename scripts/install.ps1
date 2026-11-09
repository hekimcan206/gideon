<#
.SYNOPSIS
  GIDEON installer for Windows: backend virtualenv + speech engines + models, then the Electron UI.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\install.ps1
.EXAMPLE
  scripts\install.ps1 -Dev        # also pytest, ruff, mypy
.EXAMPLE
  scripts\install.ps1 -NoUi -NoModels
#>
[CmdletBinding()]
param(
    [switch]$Dev,
    [switch]$NoUi,
    [switch]$NoModels,
    [string]$Python = ""
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

function Step([string]$Message) { Write-Host "`n==> $Message" -ForegroundColor Cyan }

function Test-Python([string[]]$Command) {
    try {
        $exe = $Command[0]
        $rest = @($Command | Select-Object -Skip 1)
        & $exe @rest -c "import sys; sys.exit(0 if (3, 12) <= sys.version_info[:2] < (3, 14) else 1)" 2>$null
        return $LASTEXITCODE -eq 0
    } catch { return $false }
}

# Prefer the Python launcher (py -3.12 / -3.13), then python on PATH.
$PyCmd = $null
if ($Python) { $PyCmd = @($Python) }
else {
    foreach ($candidate in @(@("py", "-3.12"), @("py", "-3.13"), @("python"))) {
        if (Test-Python $candidate) { $PyCmd = $candidate; break }
    }
}
if (-not $PyCmd) {
    throw "Python 3.12 or 3.13 is required (https://www.python.org/downloads/ or: winget install Python.Python.3.12)."
}
$PyExe = $PyCmd[0]
$PyArgs = @($PyCmd | Select-Object -Skip 1)
Step ("Using " + (& $PyExe @PyArgs --version))

$Venv = Join-Path $Root "backend\.venv"
Step "Creating virtual environment in backend\.venv"
& $PyExe @PyArgs -m venv $Venv
if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
$Py = Join-Path $Venv "Scripts\python.exe"
& $Py -m pip install --quiet --upgrade pip

$Extras = "engines,live"
if ($Dev) { $Extras += ",dev" }
Step "Installing the backend with extras [$Extras]"
& $Py -m pip install --quiet -e "$(Join-Path $Root 'backend')[$Extras]"
if ($LASTEXITCODE -ne 0) { throw "backend install failed" }
# One documented install path on every OS: openwakeword without its metadata dependencies (on Linux they pin
# tflite-runtime, which has no Python 3.12 wheels); the ones it really imports come with the [engines] extra.
& $Py -m pip install --quiet --no-deps openwakeword==0.6.0
if ($LASTEXITCODE -ne 0) { throw "openwakeword install failed" }

if (-not $NoModels) {
    $model = if ($env:GIDEON_STT_MODEL) { $env:GIDEON_STT_MODEL } else { "base" }
    Step "Downloading models (openWakeWord hey_jarvis, faster-whisper $model)"
    & $Py -c "import openwakeword.utils as u; u.download_models(['hey_jarvis'])"
    if ($LASTEXITCODE -ne 0) { throw "openWakeWord model download failed" }
    & $Py -c "from faster_whisper import WhisperModel; WhisperModel('$model', device='cpu', compute_type='int8')"
    if ($LASTEXITCODE -ne 0) { throw "Whisper model download failed" }
}

if (-not $NoUi) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw "Node.js 22.12+ is required for the UI (or use -NoUi)."
    }
    Step "Installing the UI (Electron, Vite) and building it"
    Push-Location (Join-Path $Root "ui")
    try {
        npm ci --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "UI build failed" }
    } finally { Pop-Location }
}

Step "Done"
Write-Host @"
Run the desktop app (Electron starts the backend with a random per-launch token):
  scripts\start.ps1
Backend only, with a microphone (from backend\):
  `$env:GIDEON_AUDIO = 'mic'; .venv\Scripts\python -m gideon serve
No microphone? Generate synthetic utterances and replay them (from backend\):
  .venv\Scripts\python -m gideon fixtures; `$env:GIDEON_AUDIO = 'wav'; .venv\Scripts\python -m gideon serve
"@
