# run_portal_sync.ps1 — Windows-native portal pipeline automation
#
# Flow: change-detection -> incremental build -> parity check -> optional
#       Portfolio_Positions ground-truth check -> diff sync.
# Schedulable via Task Scheduler. Logs per-day, pings healthchecks.io if configured.
#
# Exit code taxonomy:
#   0 — ok, or no changes detected (both normal outcomes for cron)
#   1 — build failed
#   2 — verify_vs_prod failed (local <-> prod parity drift — do NOT sync)
#   3 — sync failed
#   4 — verify_positions failed (Fidelity Portfolio_Positions ground-truth mismatch)

[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$DryRun,
    [switch]$UseLocal
)

$ErrorActionPreference = "Stop"

# ── Paths ────────────────────────────────────────────────────────────────
$ScriptDir    = Split-Path -Parent $MyInvocation.MyCommand.Path
$PipelineDir  = Split-Path -Parent $ScriptDir
$DataDir      = Join-Path $PipelineDir "data"
$DbPath       = Join-Path $DataDir "timemachine.db"
$Marker       = Join-Path $DataDir ".last_run"
$LogDir       = Join-Path $env:LOCALAPPDATA "portal\logs"
$Today        = Get-Date -Format "yyyy-MM-dd"
$LogFile      = Join-Path $LogDir "sync-$Today.log"

$Python = Join-Path $PipelineDir ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python3"
}

$Downloads = $env:PORTAL_DOWNLOADS
if (-not $Downloads) { $Downloads = Join-Path $env:USERPROFILE "Downloads" }

$Healthcheck = $env:PORTAL_HEALTHCHECK_URL

# ── Logging ──────────────────────────────────────────────────────────────
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-Log {
    param([string]$Message)
    $line = "$((Get-Date).ToString('s')) $Message"
    Add-Content -Path $LogFile -Value $line
    Write-Host $line
}

function Ping-Healthcheck {
    param([string]$Suffix = "")
    if (-not $Healthcheck) { return }
    $url = if ($Suffix) { "$Healthcheck/$Suffix" } else { $Healthcheck }
    try {
        Invoke-WebRequest -Uri $url -Method GET -TimeoutSec 10 -UseBasicParsing | Out-Null
    } catch {
        Write-Log "  healthcheck ping failed (ignored): $_"
    }
}

function Run-Python {
    param([string[]]$PyArgs)
    Write-Log "  > $Python $($PyArgs -join ' ')"
    # Scope ErrorActionPreference to Continue so native stderr doesn't throw.
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Python @PyArgs 2>&1 | ForEach-Object {
            $text = if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.ToString() } else { "$_" }
            Add-Content -Path $LogFile -Value $text
            Write-Host $text
        }
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    return $code
}

# ── Change detection ─────────────────────────────────────────────────────
function Test-ChangesDetected {
    if (-not (Test-Path $Marker)) { return $true }  # first run

    $markerTime = (Get-Item $Marker).LastWriteTime

    $qjDb = Join-Path $env:APPDATA "com.mutangtech.qianji.win\qianji_flutter\qianjiapp.db"
    if ((Test-Path $qjDb) -and ((Get-Item $qjDb).LastWriteTime -gt $markerTime)) {
        Write-Log "  Change detected: Qianji DB modified"
        return $true
    }

    foreach ($pattern in @("Accounts_History*.csv", "Bloomberg.Download*.qfx", "Robinhood_history.csv", "Portfolio_Positions*.csv")) {
        $newer = Get-ChildItem -Path $Downloads -Filter $pattern -ErrorAction SilentlyContinue |
                 Where-Object { $_.LastWriteTime -gt $markerTime }
        if ($newer) {
            Write-Log "  Change detected: new $pattern"
            return $true
        }
    }

    return $false
}

# ── Portfolio_Positions gate helper ──────────────────────────────────────
function Get-NewestPortfolioPositions {
    # Returns the FileInfo for the newest Portfolio_Positions_*.csv in Downloads
    # that is newer than the .last_run marker. Returns $null if none.
    if (-not (Test-Path $Downloads)) { return $null }

    $candidates = Get-ChildItem -Path $Downloads -Filter "Portfolio_Positions_*.csv" -ErrorAction SilentlyContinue
    if (-not $candidates) { return $null }

    if (Test-Path $Marker) {
        $markerTime = (Get-Item $Marker).LastWriteTime
        $candidates = $candidates | Where-Object { $_.LastWriteTime -gt $markerTime }
        if (-not $candidates) { return $null }
    }

    return $candidates | Sort-Object LastWriteTime -Descending | Select-Object -First 1
}

# ── Main ─────────────────────────────────────────────────────────────────
Write-Log "============================================================"
Write-Log "  Portal Sync"
Write-Log "  host=$env:COMPUTERNAME log=$LogFile"
Write-Log "============================================================"

Ping-Healthcheck "start"

if (-not $Force) {
    Write-Log "[1] Checking for data changes..."
    if (-not (Test-ChangesDetected)) {
        Write-Log "  No changes detected. Use -Force to override."
        Ping-Healthcheck    # success (no-op is a valid outcome)
        exit 0
    }
} else {
    Write-Log "[1] Force mode — skipping change detection"
}

# ── Build ────────────────────────────────────────────────────────────────
Write-Log "[2] Incremental build..."
$rc = Run-Python @("$ScriptDir\build_timemachine_db.py", "incremental")
if ($rc -ne 0) {
    Write-Log "  BUILD FAILED (exit=$rc)"
    Ping-Healthcheck "fail"
    exit 1
}

# ── Verify vs prod ───────────────────────────────────────────────────────
if (-not $UseLocal) {
    Write-Log "[3] Verifying local vs prod D1..."
    $rc = Run-Python @("$ScriptDir\verify_vs_prod.py")
    if ($rc -ne 0) {
        Write-Log "  PARITY CHECK FAILED (exit=$rc) — SYNC BLOCKED"
        Ping-Healthcheck "fail"
        exit 2
    }
}

# ── Optional Portfolio_Positions ground-truth gate ───────────────────────
$positionsCsv = Get-NewestPortfolioPositions
if ($positionsCsv) {
    Write-Log "[3b] Verifying share counts vs Fidelity Portfolio_Positions..."
    Write-Log "  Using: $($positionsCsv.FullName)"
    $rc = Run-Python @("$ScriptDir\verify_positions.py", "--positions", $positionsCsv.FullName)
    if ($rc -ne 0) {
        Write-Log "  POSITIONS CHECK FAILED (exit=$rc) — SYNC BLOCKED"
        Ping-Healthcheck "fail"
        exit 4
    }
} else {
    Write-Log "[3b] No new Portfolio_Positions CSV in Downloads — skipping ground-truth check"
}

# ── Sync ─────────────────────────────────────────────────────────────────
if ($DryRun) {
    Write-Log "[4] Dry run — skipping sync"
} else {
    Write-Log "[4] Syncing to D1 (diff mode — default)..."
    $syncArgs = @("$ScriptDir\sync_to_d1.py")
    if ($UseLocal) { $syncArgs += "--local" }
    $rc = Run-Python $syncArgs
    if ($rc -ne 0) {
        Write-Log "  SYNC FAILED (exit=$rc)"
        Ping-Healthcheck "fail"
        exit 3
    }
}

# ── Success ──────────────────────────────────────────────────────────────
(Get-Date).ToString('s') | Set-Content -Path $Marker
Write-Log "============================================================"
Write-Log "  Done"
Write-Log "============================================================"
Ping-Healthcheck
exit 0
