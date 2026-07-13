@echo off
REM Install Scrapeboard worker as a Windows Scheduled Task (runs at logon,
REM restarts on failure, no terminal required).
REM Also installs a daily auto-update task (git check → pip → restart).
REM Usage:
REM   install_service.bat
REM   install_service.bat --uninstall
REM   install_service.bat --no-auto-update

setlocal EnableExtensions
cd /d "%~dp0"
set "ROOT=%CD%"
set "REPO=%ROOT%\.."
set "TASK=ScrapeboardWorker"
set "AUTOTASK=ScrapeboardWorkerAutoUpdate"
set "UNINSTALL=0"
set "INSTALL_AUTO=1"

if /I "%~1"=="--uninstall" set "UNINSTALL=1"
if /I "%~1"=="-u" set "UNINSTALL=1"
if /I "%~1"=="--no-auto-update" set "INSTALL_AUTO=0"
if /I "%~2"=="--no-auto-update" set "INSTALL_AUTO=0"

if "%UNINSTALL%"=="1" (
  schtasks /Delete /TN "%TASK%" /F >nul 2>&1
  schtasks /Delete /TN "%AUTOTASK%" /F >nul 2>&1
  echo Uninstalled Scheduled Tasks: %TASK% and %AUTOTASK%
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

REM Daily auto-update at 04:00 local
if "%INSTALL_AUTO%"=="1" (
  set "AUTOWRAP=%ROOT%\run_auto_update.cmd"
  > "%AUTOWRAP%" echo @echo off
  >> "%AUTOWRAP%" echo cd /d "%REPO%"
  >> "%AUTOWRAP%" echo set SCRAPEBOARD_ASSUME_YES=1
  >> "%AUTOWRAP%" echo if exist "%ROOT%\.venv\Scripts\python.exe" ^(
  >> "%AUTOWRAP%" echo   "%ROOT%\.venv\Scripts\python.exe" install.py --role worker --auto-update --yes ^>^> "%ROOT%\logs\auto_update.log" 2^>^&1
  >> "%AUTOWRAP%" echo ^) else ^(
  >> "%AUTOWRAP%" echo   python install.py --role worker --auto-update --yes ^>^> "%ROOT%\logs\auto_update.log" 2^>^&1
  >> "%AUTOWRAP%" echo ^)
  schtasks /Delete /TN "%AUTOTASK%" /F >nul 2>&1
  schtasks /Create /TN "%AUTOTASK%" /TR "\"%AUTOWRAP%\"" /SC DAILY /ST 04:00 /RL HIGHEST /F
  if errorlevel 1 (
    schtasks /Create /TN "%AUTOTASK%" /TR "\"%AUTOWRAP%\"" /SC DAILY /ST 04:00 /F
  )
  if errorlevel 1 (
    echo WARN: could not create daily auto-update task %AUTOTASK%
  ) else (
    echo Installed daily auto-update task "%AUTOTASK%" at 04:00 local.
    echo   Logs: %ROOT%\logs\auto_update.log
  )
) else (
  schtasks /Delete /TN "%AUTOTASK%" /F >nul 2>&1
  echo Skipped auto-update task ^(--no-auto-update^).
)

echo.
pause
