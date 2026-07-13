# Scrapeboard Worker — Windows service installer (PowerShell)
# Prefer install_service.bat; this script offers the same Task Scheduler setup.
# Run:  powershell -ExecutionPolicy Bypass -File install_service.ps1
#       powershell -ExecutionPolicy Bypass -File install_service.ps1 -Uninstall
#       powershell -ExecutionPolicy Bypass -File install_service.ps1 -NoAutoUpdate

param(
  [switch]$Uninstall,
  [switch]$NoAutoUpdate
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = Split-Path -Parent $Root
Set-Location $Root
$TaskName = "ScrapeboardWorker"
$AutoTaskName = "ScrapeboardWorkerAutoUpdate"

if ($Uninstall) {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
  Unregister-ScheduledTask -TaskName $AutoTaskName -Confirm:$false -ErrorAction SilentlyContinue
  Write-Host "Uninstalled Scheduled Tasks: $TaskName and $AutoTaskName"
  exit 0
}

if (-not (Test-Path (Join-Path $Root "worker_config.json"))) {
  Write-Error "worker_config.json missing. Run setup_and_run.bat or: python agent.py --setup"
}

$pyw = Join-Path $Root ".venv\Scripts\pythonw.exe"
$py = Join-Path $Root ".venv\Scripts\python.exe"
if (Test-Path $pyw) { $Python = $pyw }
elseif (Test-Path $py) { $Python = $py }
else {
  $Python = (Get-Command pythonw -ErrorAction SilentlyContinue)?.Source
  if (-not $Python) { $Python = (Get-Command python).Source }
}

New-Item -ItemType Directory -Force -Path (Join-Path $Root "logs") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Root "work") | Out-Null

$wrap = Join-Path $Root "run_service.cmd"
@"
@echo off
cd /d "$Root"
"$Python" "$Root\agent.py" --service
"@ | Set-Content -Path $wrap -Encoding ASCII

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

$action = New-ScheduledTaskAction -Execute $wrap
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) `
  -StartWhenAvailable
try {
  Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -RunLevel Highest -Force | Out-Null
} catch {
  Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
}

Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Write-Host "Installed Scheduled Task '$TaskName' (At logon, auto-restart)."
Write-Host "Logs: $Root\logs\worker.log"

if (-not $NoAutoUpdate) {
  $autoPy = if (Test-Path $py) { $py } else { $Python }
  $autoWrap = Join-Path $Root "run_auto_update.cmd"
  $autoLog = Join-Path $Root "logs\auto_update.log"
  @"
@echo off
cd /d "$Repo"
set SCRAPEBOARD_ASSUME_YES=1
"$autoPy" install.py --role worker --auto-update --yes >> "$autoLog" 2>&1
"@ | Set-Content -Path $autoWrap -Encoding ASCII

  Unregister-ScheduledTask -TaskName $AutoTaskName -Confirm:$false -ErrorAction SilentlyContinue
  $autoAction = New-ScheduledTaskAction -Execute $autoWrap
  $autoTrigger = New-ScheduledTaskTrigger -Daily -At "4:00AM"
  $autoSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)
  try {
    Register-ScheduledTask -TaskName $AutoTaskName -Action $autoAction -Trigger $autoTrigger -Settings $autoSettings `
      -RunLevel Highest -Force | Out-Null
  } catch {
    Register-ScheduledTask -TaskName $AutoTaskName -Action $autoAction -Trigger $autoTrigger -Settings $autoSettings -Force | Out-Null
  }
  Write-Host "Installed daily auto-update task '$AutoTaskName' (04:00 local)."
  Write-Host "Logs: $autoLog"
} else {
  Unregister-ScheduledTask -TaskName $AutoTaskName -Confirm:$false -ErrorAction SilentlyContinue
  Write-Host "Skipped auto-update task (-NoAutoUpdate)."
}

Write-Host "Uninstall: .\install_service.ps1 -Uninstall"
