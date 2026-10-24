# Starts api (:8000), worker (:8001), console (:5173) and simulator (:5174) in this window.
# Each line is prefixed with its process name. Ctrl+C stops all four.
# Native programs write to stderr; judge them by exit code.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "backend\.venv is missing. Run: py -3.12 -m venv backend\.venv; backend\.venv\Scripts\pip install -e 'backend[dev]'" }

$procs = @(
    @{ Name = "api";       Color = "Cyan";    Dir = "backend";   Cmd = $python; Args = @("-m", "uvicorn", "app.main_api:app", "--port", "8000", "--reload") },
    @{ Name = "worker";    Color = "Magenta"; Dir = "backend";   Cmd = $python; Args = @("-m", "uvicorn", "app.main_worker:app", "--port", "8001", "--reload") },
    @{ Name = "console";   Color = "Yellow";  Dir = "frontend";  Cmd = "npm.cmd"; Args = @("run", "dev") },
    @{ Name = "simulator"; Color = "Green";   Dir = "simulator"; Cmd = "npm.cmd"; Args = @("run", "dev") }
)

$jobs = foreach ($p in $procs) {
    Start-Job -Name $p.Name -ArgumentList (Join-Path $root $p.Dir), $p.Cmd, $p.Args -ScriptBlock {
        param($dir, $cmd, $cmdArgs)
        Set-Location $dir
        & $cmd @cmdArgs 2>&1
    }
}

Write-Host "api http://localhost:8000 · worker http://localhost:8001 · console http://localhost:5173 · simulator http://localhost:5174"
try {
    while ($true) {
        foreach ($p in $procs) {
            Receive-Job -Name $p.Name -ErrorAction SilentlyContinue | ForEach-Object {
                Write-Host ("{0,-9} | " -f $p.Name) -ForegroundColor $p.Color -NoNewline
                Write-Host $_
            }
        }
        if (($jobs | Where-Object { $_.State -eq "Failed" -or $_.State -eq "Completed" }).Count -gt 0) {
            $jobs | Where-Object { $_.State -ne "Running" } | ForEach-Object { Write-Host "$($_.Name) stopped ($($_.State))" -ForegroundColor Red }
        }
        Start-Sleep -Milliseconds 200
    }
}
finally {
    $jobs | Stop-Job -ErrorAction SilentlyContinue
    $jobs | Remove-Job -Force -ErrorAction SilentlyContinue
    Write-Host "Stopped."
}
