@echo off
REM Install Scrapeboard worker as a Windows Scheduled Task (runs at logon,
REM restarts on failure, no terminal required).
REM Usage:
REM   install_service.bat
REM   install_service.bat --uninstall

setlocal EnableExtensions
cd /d "%~dp0"
set "ROOT=%CD%"
set "TASK=ScrapeboardWorker"
set "UNINSTALL=0"

if /I "%~1"=="--uninstall" set "UNINSTALL=1"
if /I "%~1"=="-u" set "UNINSTALL=1"

if "%UNINSTALL%"=="1" (
  schtasks /Delete /TN "%TASK%" /F >nul 2>&1
  echo Uninstalled Scheduled Task: %TASK%
  exit /b 0
)

if not exist "%ROOT%\worker_config.json" (
  echo ERROR: worker_config.json missing.
  echo Run setup_and_run.bat first ^(or: python agent.py --setup^).
  pause
  exit /b 1
)

set "PY="
if exist "%ROOT%\.venv\Scripts\pythonw.exe" set "PY=%ROOT%\.venv\Scripts\pythonw.exe"
if not defined PY if exist "%ROOT%\.venv\Scripts\python.exe" set "PY=%ROOT%\.venv\Scripts\python.exe"
if not defined PY (
  where pythonw >nul 2>&1 && for /f "delims=" %%P in ('where pythonw') do set "PY=%%P" & goto :havepy
)
if not defined PY (
  where python >nul 2>&1 && for /f "delims=" %%P in ('where python') do set "PY=%%P" & goto :havepy
)
:havepy
if not defined PY (
  echo ERROR: Python not found. Install Python 3.10+ and re-run setup.
  pause
  exit /b 1
)

if not exist "%ROOT%\logs" mkdir "%ROOT%\logs"
if not exist "%ROOT%\work" mkdir "%ROOT%\work"

REM Wrapper that cds into worker dir and runs agent in service mode
set "WRAP=%ROOT%\run_service.cmd"
> "%WRAP%" echo @echo off
>> "%WRAP%" echo cd /d "%ROOT%"
>> "%WRAP%" echo "%PY%" "%ROOT%\agent.py" --service
>> "%WRAP%" echo if errorlevel 1 exit /b %%ERRORLEVEL%%

schtasks /Delete /TN "%TASK%" /F >nul 2>&1
REM At logon, highest privileges if available, restart on failure
schtasks /Create /TN "%TASK%" /TR "\"%WRAP%\"" /SC ONLOGON /RL HIGHEST /F
if errorlevel 1 (
  echo Falling back to ONLOGON without highest privileges...
  schtasks /Create /TN "%TASK%" /TR "\"%WRAP%\"" /SC ONLOGON /F
)
if errorlevel 1 (
  echo ERROR: could not create Scheduled Task. Try running as Administrator.
  pause
  exit /b 1
)

REM Start immediately
schtasks /Run /TN "%TASK%" >nul 2>&1

echo.
echo Installed Windows Scheduled Task "%TASK%"
echo   Starts at user logon, runs in background ^(no console window with pythonw^).
echo   Logs: %ROOT%\logs\worker.log
echo   Stop:  install_service.bat --uninstall
echo   Or:    schtasks /End /TN %TASK%
echo.
pause
