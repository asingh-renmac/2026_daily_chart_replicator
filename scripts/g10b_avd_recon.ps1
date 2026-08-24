<#
.SYNOPSIS
  G10b Phase 0 — is this Windows host a viable place to serve the chart lane? (plan.md §14.9)

.DESCRIPTION
  Read-only. Installs nothing, changes nothing, needs no admin rights. Every check prints
  PASS / WARN / FAIL and, on anything other than PASS, what the consequence is — a host
  fact is only useful if you know which part of §14 it breaks.

  This answers the CHEAP half of Phase 0. The decisive half — does DLX keep serving a
  disconnected session, and for how long — takes hours of wall time and lives in
  `scripts/g10b_dlx_watch.py`. Run this first; run that one overnight.

.PARAMETER Python
  Interpreter used for the Haver probe. Defaults to whatever `python` resolves to.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\g10b_avd_recon.ps1
  powershell -ExecutionPolicy Bypass -File scripts\g10b_avd_recon.ps1 -Python C:\envs\haver-chart\Scripts\python.exe
#>

[CmdletBinding()]
param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Continue"
$script:Fail = 0
$script:Warn = 0

function Section($name) {
    Write-Host ""
    Write-Host "== $name " -NoNewline -ForegroundColor Cyan
    Write-Host ("=" * [Math]::Max(0, 62 - $name.Length)) -ForegroundColor Cyan
}

function Result($verdict, $label, $detail, $consequence = "") {
    switch ($verdict) {
        "PASS" { $c = "Green" }
        "WARN" { $c = "Yellow"; $script:Warn++ }
        "FAIL" { $c = "Red";    $script:Fail++ }
        default { $c = "Gray" }
    }
    Write-Host ("  {0,-4} " -f $verdict) -ForegroundColor $c -NoNewline
    Write-Host ("{0,-30} {1}" -f $label, $detail)
    if ($consequence) { Write-Host ("       -> " + $consequence) -ForegroundColor DarkGray }
}

function Get-RegValue($path, $name) {
    try { (Get-ItemProperty -Path $path -Name $name -ErrorAction Stop).$name } catch { $null }
}

Write-Host ""
Write-Host "G10b - AVD reconnaissance for the remote chart lane (plan.md 14.9 Phase 0)" -ForegroundColor White
Write-Host ("Host {0}   User {1}   {2}" -f $env:COMPUTERNAME, $env:USERNAME, (Get-Date -Format s)) -ForegroundColor DarkGray

# ---------------------------------------------------------------- 1. host identity
Section "1. Host identity"

$os = Get-CimInstance Win32_OperatingSystem
Result "INFO" "OS" ("{0} (build {1})" -f $os.Caption, $os.BuildNumber)
Result "INFO" "Memory" ("{0:N1} GB total, {1:N1} GB free" -f ($os.TotalVisibleMemorySize/1MB), ($os.FreePhysicalMemory/1MB))

$cpu = (Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors
Result "INFO" "Logical CPUs" $cpu

$sysDrive = Get-PSDrive -Name ($env:SystemDrive.TrimEnd(':')) -ErrorAction SilentlyContinue
if ($sysDrive) {
    $freeGB = [Math]::Round($sysDrive.Free / 1GB, 1)
    if ($freeGB -lt 10) {
        Result "WARN" "System drive free" "$freeGB GB" "Renders, the parquet cache and cloudflared logs all land here."
    } else {
        Result "PASS" "System drive free" "$freeGB GB"
    }
}

# AVD session-host agent is the reliable tell that this is a session host, not a plain VM.
$avdAgent = Get-Service -Name "RDAgentBootLoader" -ErrorAction SilentlyContinue
if ($avdAgent) {
    Result "INFO" "AVD session host" "yes (RDAgentBootLoader $($avdAgent.Status))" `
        "Confirms 14.2 - this is a desktop host, so treat it as a TEST BED only."
} else {
    Result "INFO" "AVD session host" "no AVD agent found - this looks like an ordinary VM"
}

# --------------------------------------------------------------- 2. persistence
Section "2. Persistence (does an install survive a reboot?)"

$fslogix = Get-Service -Name "frxsvc" -ErrorAction SilentlyContinue
if ($fslogix) {
    $vhdLocations = Get-RegValue "HKLM:\SOFTWARE\FSLogix\Profiles" "VHDLocations"
    Result "WARN" "FSLogix" "present ($($fslogix.Status))" `
        "Profile containers roam, but anything installed OUTSIDE the profile is discarded on reboot. Install the lane under the profile, or accept re-running the unzip."
    if ($vhdLocations) { Result "INFO" "FSLogix VHD location" $vhdLocations }
} else {
    Result "PASS" "FSLogix" "not present - installs outside the profile should persist"
}

$uptime = (Get-Date) - $os.LastBootUpTime
Result "INFO" "Uptime" ("{0:N1} days (last boot {1:yyyy-MM-dd HH:mm})" -f $uptime.TotalDays, $os.LastBootUpTime)

# --------------------------------------------------- 3. session / idle timeouts
Section "3. Session policy (what will kill the server process)"

$tsPolicy = "HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services"
$limits = @{
    "MaxIdleTime"          = "Idle -> disconnect"
    "MaxDisconnectionTime" = "Disconnected -> SIGN OUT"
    "MaxConnectionTime"    = "Total session cap"
}
$anyLimit = $false
foreach ($k in $limits.Keys) {
    $v = Get-RegValue $tsPolicy $k
    if ($null -ne $v -and $v -gt 0) {
        $anyLimit = $true
        $mins = [Math]::Round($v / 60000, 0)
        $verdict = if ($k -eq "MaxDisconnectionTime") { "FAIL" } else { "WARN" }
        Result $verdict $limits[$k] "$mins min" `
            $(if ($k -eq "MaxDisconnectionTime") {
                "This is the one that matters. A sign-out ends the session and kills the server. It caps how long the pilot can stay up and is the direct argument for G10i."
            } else { "Disconnect alone is survivable; sign-out is not." })
    }
}
if (-not $anyLimit) {
    Result "PASS" "Session time limits" "no GPO limits set at the machine policy key" `
        "Check user-level and Entra/Intune policy too - this key is not the only place limits live."
}

Write-Host ""
Write-Host "  Current sessions (qwinsta):" -ForegroundColor DarkGray
try { qwinsta 2>$null | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray } } catch {}

# ------------------------------------------------------------------- 4. python
Section "4. Python"

$pyOk = $false
try {
    $pyVer = & $Python --version 2>&1
    $pyPath = & $Python -c "import sys; print(sys.executable)" 2>&1
    if ($LASTEXITCODE -eq 0) {
        $pyOk = $true
        Result "PASS" "Interpreter" "$pyVer"
        Result "INFO" "Path" "$pyPath"
        $verNum = & $Python -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>&1
        if ([version]$verNum -lt [version]"3.10") {
            Result "FAIL" "Version" "$verNum" "The lane requires 3.10+."
        }
    }
} catch {}
if (-not $pyOk) {
    Result "FAIL" "Interpreter" "'$Python' did not run" `
        "Install Python 3.10+ or pass -Python <path>. Everything below this line is skipped."
}

# ---------------------------------------------------------------- 5. Haver / DLX
Section "5. Haver DLX"

$dlxDirs = @(
    "C:\Program Files (x86)\Haver Analytics",
    "C:\Program Files\Haver Analytics",
    "$env:LOCALAPPDATA\Haver Analytics",
    "$env:LOCALAPPDATA\Programs\Haver Analytics"
) | Where-Object { Test-Path $_ }

if ($dlxDirs) {
    foreach ($d in $dlxDirs) { Result "PASS" "DLX install" $d }
} else {
    Result "WARN" "DLX install" "not found in the usual locations" `
        "The Python probe below is authoritative - a non-standard install path is fine."
}

$dlxProc = Get-Process -Name "DLX*" -ErrorAction SilentlyContinue
if ($dlxProc) {
    $names = ($dlxProc | Select-Object -ExpandProperty ProcessName -Unique) -join ", "
    Result "INFO" "DLX process" $names
} else {
    Result "INFO" "DLX process" "not currently running"
}

if ($pyOk) {
    # Via a temp FILE, not `python -c`. PowerShell rewrites quoting when it hands an
    # argument to a native executable, so the probe's string literals arrive unquoted and
    # Python reports a SyntaxError that looks like a Haver problem.
    $probe = @'
import json, sys, warnings
out = {"import": False, "direct": False, "pull": False, "rows": 0, "last": None, "error": None}
try:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import Haver
    out["import"] = True
    Haver.direct("on")
    out["direct"] = True
    df = Haver.data(["LR"], "USECON", startdate="2025-01-01")
    if df is None or isinstance(df, dict):
        out["error"] = "Haver returned %r instead of a frame" % (df,)
    else:
        out["pull"] = True
        out["rows"] = int(len(df))
        out["last"] = str(df.index[-1])
except Exception as exc:
    out["error"] = "%s: %s" % (type(exc).__name__, exc)
print("PROBE" + json.dumps(out))
'@
    $probeFile = Join-Path $env:TEMP ("g10b_probe_{0}.py" -f [guid]::NewGuid().ToString("N"))
    Set-Content -Path $probeFile -Value $probe -Encoding UTF8
    try {
        $raw = & $Python $probeFile 2>&1
    } finally {
        Remove-Item $probeFile -ErrorAction SilentlyContinue
    }
    $line = ($raw | Where-Object { $_ -like "PROBE*" } | Select-Object -First 1)
    if ($line) {
        $p = ($line -replace "^PROBE", "") | ConvertFrom-Json
        if ($p.import) { Result "PASS" "import Haver" "ok" } else { Result "FAIL" "import Haver" $p.error "pip install Haver into this interpreter." }
        if ($p.direct) { Result "PASS" "Haver.direct('on')" "ok" }
        if ($p.pull) {
            Result "PASS" "Live pull LR@USECON" ("{0} rows, last {1}" -f $p.rows, $p.last) `
                "DLX answers a non-interactive caller. This is the single most important line in the report."
        } elseif ($p.import) {
            Result "FAIL" "Live pull LR@USECON" $p.error `
                "If this needs an interactive login or 2FA, G10b fails and 14 needs a different DLX story."
        }
    } else {
        Result "FAIL" "Haver probe" "no output" ("Raw: " + ($raw -join " | "))
    }
} else {
    Result "WARN" "Haver probe" "skipped (no interpreter)"
}

# --------------------------------------------------------------- 6. outbound net
Section "6. Outbound network (for the Cloudflare tunnel)"

# cloudflared prefers QUIC on UDP 7844 and falls back to HTTP/2 on TCP 443.
# Test-NetConnection cannot test UDP usefully, so TCP 7844 is the proxy for "is the
# argotunnel edge reachable at all"; TCP 443 is the fallback that actually has to work.
$targets = @(
    @{ Host = "region1.v2.argotunnel.com"; Port = 7844; Why = "cloudflared preferred edge" },
    @{ Host = "region1.v2.argotunnel.com"; Port = 443;  Why = "cloudflared http2 fallback" },
    @{ Host = "api.cloudflare.com";        Port = 443;  Why = "tunnel control plane" }
)
foreach ($t in $targets) {
    $r = Test-NetConnection -ComputerName $t.Host -Port $t.Port -InformationLevel Quiet -WarningAction SilentlyContinue
    if ($r) {
        Result "PASS" ("{0}:{1}" -f $t.Host, $t.Port) $t.Why
    } else {
        Result "WARN" ("{0}:{1}" -f $t.Host, $t.Port) ("blocked - " + $t.Why) `
            "If BOTH 7844 and 443 are blocked, cloudflared cannot connect and 14.6 needs a different edge."
    }
}

$winhttp = netsh winhttp show proxy 2>$null
if ($winhttp -match "Direct access") {
    Result "PASS" "WinHTTP proxy" "direct access (no proxy)"
} else {
    Result "WARN" "WinHTTP proxy" (($winhttp | Where-Object { $_ -match "Proxy" }) -join "; ") `
        "cloudflared honours HTTPS_PROXY but only over HTTP/2, never QUIC."
}
foreach ($v in @("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")) {
    $val = [Environment]::GetEnvironmentVariable($v)
    if ($val) { Result "INFO" "env $v" $val }
}

# ------------------------------------------------------- 7. rights and resources
Section "7. Rights and shared resources"

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = ([Security.Principal.WindowsPrincipal]$id).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if ($isAdmin) {
    Result "PASS" "Local administrator" "yes - cloudflared can install as a Windows service"
} else {
    Result "WARN" "Local administrator" "no" `
        "cloudflared can still run in the foreground for the pilot; it just will not survive a sign-out as a service."
}

if (Test-Path "P:\Public") {
    Result "PASS" "P:\Public" "reachable" "Copy the knowledge stores LOCALLY anyway - 14.8 explains why."
} else {
    Result "WARN" "P:\Public" "not mapped for this session" `
        "Not a blocker. CLARIFIED_KNOWLEDGE_DIR should point at a local copy on a server."
}

$cf = Get-Command cloudflared -ErrorAction SilentlyContinue
if ($cf) { Result "INFO" "cloudflared" $cf.Source } else { Result "INFO" "cloudflared" "not installed yet (expected at this stage)" }

# ------------------------------------------------------------------- verdict
Section "Verdict"

if ($script:Fail -gt 0) {
    Write-Host "  $($script:Fail) FAIL, $($script:Warn) WARN." -ForegroundColor Red
    Write-Host "  Do not start Phase 1. Resolve the FAIL lines first - a failed Haver probe in" -ForegroundColor Red
    Write-Host "  particular means G10b is not passable on this host." -ForegroundColor Red
} elseif ($script:Warn -gt 0) {
    Write-Host "  0 FAIL, $($script:Warn) WARN." -ForegroundColor Yellow
    Write-Host "  Viable as a test bed. Read each WARN - most describe a limit on how long the" -ForegroundColor Yellow
    Write-Host "  pilot can stay up, which is exactly the evidence G10i needs." -ForegroundColor Yellow
} else {
    Write-Host "  0 FAIL, 0 WARN. Host looks clear." -ForegroundColor Green
}

Write-Host ""
Write-Host "  Next: the decisive test. Start the durability watcher, then DISCONNECT the" -ForegroundColor White
Write-Host "  remote-desktop window WITHOUT signing out, and read the log tomorrow:" -ForegroundColor White
Write-Host "      $Python scripts\g10b_dlx_watch.py --hours 16" -ForegroundColor White
Write-Host ""

exit $(if ($script:Fail -gt 0) { 1 } else { 0 })
