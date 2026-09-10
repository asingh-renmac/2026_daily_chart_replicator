# Install Win X-13 (X-13ARIMA-SEATS) on the AVD host so `sa(...)` formulas can run.
# Previews by default, writes with -Apply.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File avd_install_x13.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File avd_install_x13.ps1 -Apply
#
# Version is PINNED, not "latest". winx13html_v3-3 is the build already validated on the
# laptop, and the download is checked against its SHA256 so the AVD cannot silently end
# up on a different X-13 than the one the numbers were developed against. If Census
# publishes v3-4 and this hash stops matching, that is the script working.
#
# THE BINARY NAME IS THE TRAP. This zip ships `x13as_html.exe` (underscore); older HTML
# builds shipped `x13ashtml.exe` (none). statsmodels looks for neither -- it wants
# `x13as.exe` and otherwise SILENTLY falls back to classical decomposition. The copy
# below is what prevents a chart labelled "seasonally adjusted (X-13)" from carrying
# numbers X-13 never produced.
param(
    [switch]$Apply,
    [string]$ToolsRoot = "C:\Users\madz\Work\asingh\tools"
)

$ErrorActionPreference = "Stop"

$url    = "https://www2.census.gov/software/x-13arima-seats/win-x-13/download/winx13html_v3-3.zip"
$sha256 = "BA1C69BCC09047E7E63DD0ED1D0E9823CD575FB37CD1D4B101BEC57B399F523B"
$dest   = Join-Path $ToolsRoot "winx13"
$x13dir = Join-Path $dest "x13as"

"tools root  : $ToolsRoot"
"install to  : $dest"
"x13as dir   : $x13dir"
"source      : $url"
""

if (Test-Path (Join-Path $x13dir "x13as.exe")) {
    "ALREADY INSTALLED: $(Join-Path $x13dir 'x13as.exe')"
    "Nothing to do. Delete $dest first if you want a clean re-install."
    exit 0
}

if (-not $Apply) {
    "PREVIEW ONLY -- re-run with -Apply to download and install."
    exit 0
}

if (-not (Test-Path $ToolsRoot)) { New-Item -ItemType Directory -Path $ToolsRoot -Force | Out-Null }

$tmp = Join-Path $env:TEMP "winx13html_v3-3.zip"
"downloading ..."
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing

$got = (Get-FileHash -Path $tmp -Algorithm SHA256).Hash
if ($got -ne $sha256) {
    Remove-Item $tmp -Force
    throw "SHA256 mismatch. expected $sha256, got $got. Census may have republished; " +
          "verify against the laptop's copy before changing the pin."
}
"sha256 ok   : $got"

# The zip holds a single top-level WinX13\ folder; its CONTENTS go straight into
# tools\winx13 so the layout matches the laptop (winx13\x13as\, winx13\WinX13.exe).
$stage = Join-Path $env:TEMP "winx13_stage"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
Expand-Archive -Path $tmp -DestinationPath $stage -Force

$inner = Join-Path $stage "WinX13"
if (-not (Test-Path $inner)) { throw "unexpected zip layout: no WinX13\ under $stage" }
if (-not (Test-Path $dest)) { New-Item -ItemType Directory -Path $dest -Force | Out-Null }
Copy-Item -Path (Join-Path $inner "*") -Destination $dest -Recurse -Force
"unpacked to : $dest"

# Rename the shipped binary to what statsmodels' _find_x12() actually looks for.
$shipped = Get-ChildItem -Path $x13dir -Filter "x13as*.exe" -ErrorAction SilentlyContinue |
           Where-Object { $_.Name -ne "x13as.exe" } | Select-Object -First 1
if (-not $shipped) { throw "no x13as*.exe found under $x13dir" }
Copy-Item -Path $shipped.FullName -Destination (Join-Path $x13dir "x13as.exe") -Force
"binary      : copied $($shipped.Name) -> x13as.exe"

# Belt and braces: transforms.py already probes this exact path in _X13_CANDIDATES, so
# the lane works without the variable. Setting it makes an ad-hoc python -c run work too.
[Environment]::SetEnvironmentVariable("X13PATH", $x13dir, "User")
"X13PATH     : set (User) to $x13dir"

Remove-Item $tmp -Force
Remove-Item $stage -Recurse -Force

""
"INSTALLED. Verify with:"
"  cd C:\Users\madz\Work\asingh\haver-chart\repo"
"  python -c ""import sys; sys.path.insert(0,'src'); import x13_seasonal_adjust as X; X.setup_x13()"""
""
"The running lane will not see X13PATH until it restarts (nightly 02:30, or restart it"
"now). It does not need to: the path is already in transforms._X13_CANDIDATES."
