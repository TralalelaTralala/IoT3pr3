@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" py -3 -m venv .venv
call ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
  echo Dependency install failed. Check Python and internet access.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" run_part3.py
pause
