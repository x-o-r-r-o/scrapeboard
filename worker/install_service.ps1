# Scrapeboard Worker — Windows service installer (PowerShell)
# Prefer install_service.bat; this script offers the same Task Scheduler setup.
# Run:  powershell -ExecutionPolicy Bypass -File install_service.ps1
#       powershell -ExecutionPolicy Bypass -File install_service.ps1 -Uninstall

param([switch]$Uninstall)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$TaskName = "ScrapeboardWorker"

if ($Uninstall) {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
  Write-Host "Uninstalled Scheduled Task: $TaskName"
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
Write-Host "Uninstall: .\install_service.ps1 -Uninstall"
