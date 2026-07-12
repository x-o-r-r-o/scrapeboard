@echo off
REM Scrapeboard — Windows entry (calls install.py)
REM Double-click or:  install.bat
REM Automated:  install.bat --role worker --yes

setlocal EnableExtensions
cd /d "%~dp0"

set "ASSUME_YES=0"
echo %* | findstr /I /C:"--yes" /C:"-y" /C:"/Y" >nul 2>&1
if not errorlevel 1 set "ASSUME_YES=1"
if /I "%SCRAPEBOARD_ASSUME_YES%"=="1" set "ASSUME_YES=1"

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
    echo ERROR: Python 3.10+ not found and winget is unavailable.
    echo Install from https://www.python.org/downloads/
    echo Enable "Add python.exe to PATH" during setup.
    if "%ASSUME_YES%"=="0" pause
    exit /b 1
  )
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  if errorlevel 1 (
    echo ERROR: winget could not install Python.
    if "%ASSUME_YES%"=="0" pause
    exit /b 1
  )
  set "PATH=%LocalAppData%\Programs\Python\Python312;%LocalAppData%\Programs\Python\Python312\Scripts;%PATH%"
  where py >nul 2>&1
  if not errorlevel 1 set "PY=py -3"
  if not defined PY (
    where python >nul 2>&1
    if not errorlevel 1 set "PY=python"
  )
  if not defined PY (
    echo ERROR: Python installed but not on PATH. Open a new terminal and re-run install.bat
    if "%ASSUME_YES%"=="0" pause
    exit /b 1
  )
)

%PY% "%~dp0install.py" %*
exit /b %ERRORLEVEL%
