$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".build-env\Scripts\python.exe")) {
    py -3 -m venv .build-env
}

& ".build-env\Scripts\python.exe" -m pip install --upgrade pip
& ".build-env\Scripts\python.exe" -m pip install -r requirements-runtime.txt
& ".build-env\Scripts\python.exe" -m PyInstaller --clean --noconfirm PhantomLimb.spec

Write-Host "Build complete: $((Get-Location).Path)\dist\PhantomLimb.exe"
