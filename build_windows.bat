@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul || (
  echo Python is required only on the build machine.
  exit /b 1
)

if not exist ".build-env\Scripts\python.exe" (
  py -3 -m venv .build-env || exit /b 1
)

".build-env\Scripts\python.exe" -m pip install --upgrade pip || exit /b 1
".build-env\Scripts\python.exe" -m pip install -r requirements-runtime.txt || exit /b 1
".build-env\Scripts\python.exe" -m PyInstaller --clean --noconfirm PhantomLimb.spec || exit /b 1

echo.
echo Build complete: %CD%\dist\PhantomLimb.exe
echo Copy that single file to the client computer.
endlocal
