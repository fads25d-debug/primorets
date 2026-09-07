@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Create the environment first. See README.md.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m primorets --data-dir "%~dp0data"
if errorlevel 1 pause

