# Runs every check CI runs. -Integration also runs tests that need the local Supabase stack (Docker).
param([switch]$Integration)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root "backend\.venv\Scripts\python.exe"

function Step($name, [scriptblock]$block) {
    Write-Host "== $name" -ForegroundColor Cyan
    & $block
    if ($LASTEXITCODE -ne 0) { throw "$name failed" }
}

Push-Location (Join-Path $root "backend")
try {
    Step "backend: ruff" { & $python -m ruff check . }
    Step "backend: mypy" { & $python -m mypy app }
    if ($Integration) { Step "backend: pytest (all)" { & $python -m pytest -q } }
    else { Step "backend: pytest (unit)" { & $python -m pytest -q -m "not integration" } }
}
finally { Pop-Location }

foreach ($app in "frontend", "simulator") {
    Push-Location (Join-Path $root $app)
    try {
        Step "${app}: typecheck" { npm.cmd run typecheck --silent }
        Step "${app}: lint" { npm.cmd run lint --silent }
        Step "${app}: test" { npm.cmd test --silent }
        Step "${app}: build" { npx.cmd vite build --logLevel warn }
    }
    finally { Pop-Location }
}
Write-Host "All checks passed." -ForegroundColor Green
