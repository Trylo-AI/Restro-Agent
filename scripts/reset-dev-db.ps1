# Wipes the dev database's app data and re-seeds Kaapi House with open orders (-NoLive to skip them).
# Refuses to run against anything but APP_ENV=local with only the seed's cafés.
param([switch]$NoLive)
# Native programs write progress to stderr; judge them by exit code, not by stderr.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root "backend\.venv\Scripts\python.exe"

& $python (Join-Path $root "supabase\seed\reset.py")
if ($LASTEXITCODE -ne 0) { Write-Error "reset failed"; exit 1 }

$seedArgs = @((Join-Path $root "supabase\seed\generate.py"), "--dev-sql")
if (-not $NoLive) { $seedArgs += "--live" }
& $python @seedArgs
if ($LASTEXITCODE -ne 0) { Write-Error "seed failed (re-run: a slow network can time out the Auth API)"; exit 1 }
