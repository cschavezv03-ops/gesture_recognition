# Prepara el entorno en Windows: crea el venv, instala dependencias y descarga
# el modelo. Ejecutar desde PowerShell, en la raíz del proyecto.
#
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1

$ErrorActionPreference = "Stop"

$VenvPath = "C:\venvs\gesture"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

if (-not (Test-Path $VenvPath)) {
    Write-Host "Creando entorno virtual en $VenvPath ..." -ForegroundColor Cyan
    py -3 -m venv $VenvPath
} else {
    Write-Host "El entorno virtual ya existe en $VenvPath" -ForegroundColor DarkGray
}

$Python = Join-Path $VenvPath "Scripts\python.exe"

Write-Host "Instalando dependencias ..." -ForegroundColor Cyan
& $Python -m pip install --upgrade pip
& $Python -m pip install -r (Join-Path $ProjectRoot "requirements.txt")

Write-Host "Descargando el modelo ..." -ForegroundColor Cyan
& $Python (Join-Path $ProjectRoot "scripts\download_model.py")

Write-Host ""
Write-Host "Listo. Para arrancar:" -ForegroundColor Green
Write-Host "  $Python `"$ProjectRoot\run.py`""
