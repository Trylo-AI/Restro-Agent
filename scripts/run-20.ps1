# S16: 20 simulated customers ordering at once (taps only, no model), plus 3 that order by free text through the
# real model, then database checks: one order per Confirm, no duplicates, nothing left unprocessed.
# Needs the api and worker running (scripts/dev.ps1). -Real 0 skips the model runs (e.g. when the quota is used up).
param([int]$Runs = 20, [int]$Real = 3, [int]$Parallel = 5)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root "backend\.venv\Scripts\python.exe"
$env:PYTHONIOENCODING = "utf-8"
Push-Location (Join-Path $root "backend")
try {
    & $python (Join-Path $root "scripts\run_conversations.py") --runs $Runs --real $Real --parallel $Parallel
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
