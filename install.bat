@echo off
REM Scrapeboard — Windows entry (calls install.py)
REM Double-click or:  install.bat

setlocal EnableExtensions
cd /d "%~dp0"

where py >nul 2>&1
if not errorlevel 1 (
  py -3 "%~dp0install.py" %*
  exit /b %ERRORLEVEL%
)

where python >nul 2>&1
if not errorlevel 1 (
  python "%~dp0install.py" %*
  exit /b %ERRORLEVEL%
)

echo ERROR: Python 3.10+ not found.
echo Install from https://www.python.org/downloads/
echo Enable "Add python.exe to PATH" during setup.
pause
exit /b 1
