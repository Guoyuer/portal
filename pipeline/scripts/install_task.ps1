# Install Windows Task Scheduler job for automatic data sync to Cloudflare R2.
# Runs sync.py daily at 9AM and on user logon.
#
# Prerequisites:
#   npm install -g wrangler; wrangler login
#   pip install -r pipeline\requirements.txt
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File pipeline\scripts\install_task.ps1
#   powershell -ExecutionPolicy Bypass -File pipeline\scripts\install_task.ps1 -Remove

param([switch]$Remove)

$TaskName = "PortalSync"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SyncScript = Join-Path $ScriptDir "sync.py"
$Python = "python"

# Try venv first
$VenvPython = Join-Path (Split-Path $ScriptDir) ".venv\Scripts\python.exe"
if (Test-Path $VenvPython) { $Python = $VenvPython }

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed $TaskName"
    exit 0
}

# Verify prerequisites
if (-not (Get-Command wrangler -ErrorAction SilentlyContinue)) {
    Write-Error "wrangler not found. Install with: npm install -g wrangler; wrangler login"
    exit 1
}

$Action = New-ScheduledTaskAction -Execute $Python -Argument "`"$SyncScript`""

$Triggers = @(
    $(New-ScheduledTaskTrigger -Daily -At 9am),
    $(New-ScheduledTaskTrigger -AtLogOn)
)

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Triggers `
    -Settings $Settings `
    -Description "Sync Fidelity CSVs + Qianji DB to Cloudflare R2" `
    -Force

Write-Host "Installed $TaskName"
Write-Host "  Python: $Python"
Write-Host "  Script: $SyncScript"
Write-Host "  Schedule: daily 9AM + on logon"
