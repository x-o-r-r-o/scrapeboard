@echo off
REM Scrapeboard worker — Windows first-run setup + selftest + start
REM Double-click or run from cmd:  setup_and_run.bat
REM Automated:  setup_and_run.bat /Y
REM   or set SCRAPEBOARD_ASSUME_YES=1
REM Wizard env: SCRAPEBOARD_PANEL_URL  SCRAPEBOARD_TOKEN
REM Optional:   SCRAPEBOARD_TAILSCALE=1

setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "ASSUME_YES=0"
set "WANT_TAILSCALE=0"
:parse_args
if "%~1"=="" goto :args_done
if /I "%~1"=="/Y" set "ASSUME_YES=1"
if /I "%~1"=="-y" set "ASSUME_YES=1"
if /I "%~1"=="--yes" set "ASSUME_YES=1"
if /I "%~1"=="--tailscale" set "WANT_TAILSCALE=1"
shift
goto :parse_args
:args_done

if /I "%SCRAPEBOARD_ASSUME_YES%"=="1" set "ASSUME_YES=1"
if /I "%SCRAPEBOARD_ASSUME_YES%"=="true" set "ASSUME_YES=1"
if /I "%SCRAPEBOARD_ASSUME_YES%"=="yes" set "ASSUME_YES=1"
if /I "%SCRAPEBOARD_TAILSCALE%"=="1" set "WANT_TAILSCALE=1"
if /I "%SCRAPEBOARD_TAILSCALE%"=="true" set "WANT_TAILSCALE=1"

if "%ASSUME_YES%"=="1" set "SCRAPEBOARD_ASSUME_YES=1"
if "%WANT_TAILSCALE%"=="1" set "SCRAPEBOARD_TAILSCALE=1"

echo ================================================================
echo  Scrapeboard Worker — Windows setup
echo ================================================================
echo Working dir: %CD%
if "%ASSUME_YES%"=="1" echo Mode: noninteractive (/Y or SCRAPEBOARD_ASSUME_YES=1)
echo.

set "PY="
where py >nul 2>&1
if not errorlevel 1 (
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
  if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
  where python >nul 2>&1
  if not errorlevel 1 (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if not errorlevel 1 set "PY=python"
  )
)

if not defined PY (
  echo Python 3.10+ not found. Trying winget install Python.Python.3.12 ...
  where winget >nul 2>&1
  if errorlevel 1 (
    echo ERROR: python not found and winget is unavailable.
    echo Install from https://www.python.org/downloads/
    echo Enable "Add python.exe to PATH" during setup.
    if "%ASSUME_YES%"=="0" pause
    exit /b 1
  )
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  if errorlevel 1 (
    echo ERROR: winget install Python failed.
    echo Install from https://www.python.org/downloads/
    if "%ASSUME_YES%"=="0" pause
    exit /b 1
  )
  echo Installed Python via winget. Refreshing PATH for this session...
  set "PATH=%LocalAppData%\Programs\Python\Python312;%LocalAppData%\Programs\Python\Python312\Scripts;%PATH%"
  where py >nul 2>&1
  if not errorlevel 1 set "PY=py -3"
  if not defined PY (
    where python >nul 2>&1
    if not errorlevel 1 set "PY=python"
  )
  if not defined PY (
    echo ERROR: Python still not on PATH. Open a new terminal and re-run.
    if "%ASSUME_YES%"=="0" pause
    exit /b 1
  )
)

echo Using: %PY%
%PY% --version

echo.
echo --- Creating virtual environment (.venv) ---
if exist ".venv\Scripts\python.exe" (
  echo Using existing .venv
) else (
  %PY% -m venv .venv
  if errorlevel 1 (
    echo venv failed
    if "%ASSUME_YES%"=="0" pause
    exit /b 1
  )
)
call .venv\Scripts\activate.bat

echo --- Installing requirements ---
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo pip install failed
  if "%ASSUME_YES%"=="0" pause
  exit /b 1
)

echo --- Playwright Chromium (first run) ---
python -m playwright install chromium

echo.
echo --- Selftest (chrome) ---
python agent.py --selftest --engine chrome
echo selftest exit: %ERRORLEVEL%

if exist "worker_config.json" goto :have_config

echo.
echo --- First-run wizard ^(creates worker_config.json^) ---
if "%ASSUME_YES%"=="1" if not defined SCRAPEBOARD_TOKEN (
  echo ERROR: noninteractive setup requires SCRAPEBOARD_TOKEN
  echo   set SCRAPEBOARD_PANEL_URL=https://your-panel
  echo   set SCRAPEBOARD_TOKEN=...
  exit /b 1
)
python -c "from agent import bootstrap_agent_deps, run_setup_wizard; bootstrap_agent_deps(); run_setup_wizard()"
if errorlevel 1 (
  if "%ASSUME_YES%"=="0" pause
  exit /b 1
)

:have_config
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

if "%ASSUME_YES%"=="1" (
  echo Noninteractive: installing background service...
  call install_service.bat
  echo.
  echo Setup summary: venv + deps + service attempted. Manual if needed: Tailscale login, panel token.
  exit /b 0
)

set /p ANS="Install background service now? [y/N] "
if /I "!ANS!"=="y" goto :install_svc
if /I "!ANS!"=="yes" goto :install_svc
goto :fg_start

:install_svc
call install_service.bat
exit /b 0

:fg_start
if "%ASSUME_YES%"=="1" (
  echo Noninteractive setup finished. Start later: python agent.py  or  install_service.bat
  exit /b 0
)
echo.
echo --- Starting worker in this window ^(Ctrl+C to stop^) ---
echo Tip: later run  install_service.bat  for a logon background service.
python agent.py
pause
