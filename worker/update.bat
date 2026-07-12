@echo off
REM Scrapeboard worker — update this scrape host (role-based sparse git pull).
REM Usage: worker\update.bat
REM        worker\update.bat --force-role

setlocal EnableExtensions
cd /d "%~dp0\.."

if exist ".scrapeboard-role" (
  set /p ROLE=<".scrapeboard-role"
)
if defined SCRAPEBOARD_ROLE set "ROLE=%SCRAPEBOARD_ROLE%"

if /I "%ROLE%"=="panel" (
  echo ERROR: this machine is marked as panel.
  echo Refusing worker update. Use deploy\hestiacp\update.sh on the panel VPS.
  echo To reconfigure: py -3 install.py --role worker --force-role --update
  exit /b 1
)

where py >nul 2>&1
if not errorlevel 1 (
  py -3 "%~dp0..\install.py" --role worker --update %*
  exit /b %ERRORLEVEL%
)

where python >nul 2>&1
if not errorlevel 1 (
  python "%~dp0..\install.py" --role worker --update %*
  exit /b %ERRORLEVEL%
)

echo ERROR: Python 3.10+ not found.
exit /b 1
