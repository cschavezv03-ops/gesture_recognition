# Arranca el control por gestos usando el venv de Windows.
#
#   powershell -ExecutionPolicy Bypass -File scripts\run.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\run.ps1 --dry-run
#
# Los argumentos se pasan tal cual a run.py.

$ErrorActionPreference = "Stop"

$Python = "C:\venvs\gesture\Scripts\python.exe"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

if (-not (Test-Path $Python)) {
    Write-Error "No existe $Python. Ejecuta primero scripts\setup.ps1"
}

& $Python (Join-Path $ProjectRoot "run.py") @args
