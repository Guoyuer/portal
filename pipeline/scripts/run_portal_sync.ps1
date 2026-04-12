# run_portal_sync.ps1 — Task Scheduler shim; all orchestration lives in run_automation.py.
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$PyArgs)
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path (Split-Path $ScriptDir -Parent) ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "python3" }
& $Python "$ScriptDir\run_automation.py" @PyArgs
exit $LASTEXITCODE
