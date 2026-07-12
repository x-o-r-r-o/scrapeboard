@echo off
REM Scrapeboard worker — Windows first-run setup + selftest + start
REM Double-click or run from cmd:  setup_and_run.bat

setlocal EnableExtensions EnableDelayedExpansion
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

if not exist "worker_config.json" (
  echo.
  echo --- First-run wizard ^(creates worker_config.json^) ---
  python -c "from agent import bootstrap_agent_deps, run_setup_wizard; bootstrap_agent_deps(); run_setup_wizard()"
)

if not exist "worker_config.json" goto :fg_start

echo.
echo ================================================================
echo  Background service ^(recommended^)
echo  Starts at logon, keeps running after this window closes,
echo  and waits for panel jobs.
echo ================================================================
echo   Install:   install_service.bat
echo   Uninstall: install_service.bat --uninstall
echo   Logs:      logs\worker.log
echo.
set /p ANS="Install background service now? [y/N] "
if /I "!ANS!"=="y" goto :install_svc
if /I "!ANS!"=="yes" goto :install_svc
goto :fg_start

:install_svc
call install_service.bat
exit /b 0

:fg_start
echo.
echo --- Starting worker in this window ^(Ctrl+C to stop^) ---
echo Tip: later run  install_service.bat  for a logon background service.
python agent.py
pause
