@echo off
REM Scrapeboard worker — Windows first-run setup + selftest + start
REM Double-click or run from cmd:  setup_and_run.bat

setlocal
cd /d "%~dp0"

echo ================================================================
echo  Scrapeboard Worker — Windows setup
echo ================================================================
echo Working dir: %CD%
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo ERROR: python not found. Install from https://www.python.org/downloads/
  echo Enable "Add python.exe to PATH" during setup.
  pause
  exit /b 1
)

python --version

echo.
echo --- Creating virtual environment (.venv) ---
python -m venv .venv
if errorlevel 1 (
  echo venv failed
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat

echo --- Installing requirements ---
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo pip install failed
  pause
  exit /b 1
)

echo --- Playwright Chromium (first run) ---
python -m playwright install chromium

echo.
echo --- Selftest (chrome) ---
python agent.py --selftest --engine chrome
echo selftest exit: %ERRORLEVEL%

echo.
echo --- Starting worker (wizard if no worker_config.json) ---
python agent.py
pause
