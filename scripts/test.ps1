# Runs every check CI runs. -Integration also runs the database tests against the hosted dev project
# (each test uses its own throwaway café; seeded data is never touched).
param([switch]$Integration)
# Native programs write to stderr; judge them by exit code.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root "backend\.venv\Scripts\python.exe"

function EnvValue($file, $name) {
    $line = Get-Content $file | Where-Object { $_ -like "$name=*" } | Select-Object -First 1
    if ($line) { return $line.Substring($name.Length + 1) }
}

if ($Integration) {
    $backendEnv = Join-Path $root "backend\.env"
    $consoleEnv = Join-Path $root "frontend\.env.local"
    $env:INTEGRATION_DATABASE_URL = EnvValue $backendEnv "DATABASE_URL_SESSION"
    $env:RLS_SUPABASE_URL = EnvValue $consoleEnv "VITE_SUPABASE_URL"
    $env:RLS_PUBLISHABLE_KEY = EnvValue $consoleEnv "VITE_SUPABASE_PUBLISHABLE_KEY"
    $env:RLS_PASSWORD = EnvValue $backendEnv "SEED_STAFF_PASSWORD"
}

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
