# Where does the host keep the knowledge store, and can THIS account reach it?
#
# Worth establishing before touching the store, because the share's failure mode on this
# box is a HANG rather than an error, and a hung probe leaves a stray process behind.
#
# On redaction: matching credential-shaped NAMES is not enough. A connection string is
# named for what it connects to, not for the secret inside it, so
# NEON_READONLY_DATABASE_URL passes a name filter and prints its password -- which is
# exactly what happened on 2026-09-06 and cost a rotation. Anything holding `user:pass@`
# is masked on the VALUE, whatever it is called.
Set-Location "C:\Users\madz\Work\asingh\haver-chart\repo"

function Mask([string]$value) {
    # scheme://user:secret@host  ->  scheme://user:***@host
    $masked = [regex]::Replace($value, '(?<=://)([^:/@\s]+):([^@/\s]+)(?=@)', '$1:***')
    # any remaining query-string secret
    [regex]::Replace($masked, '(?i)((?:password|token|key|secret)=)[^&;\s]+', '$1***')
}

"=== path-shaped settings in config\.env ==="
Get-Content "config\.env" -EA SilentlyContinue | ForEach-Object {
    if ($_ -match "^\s*#" -or $_ -notmatch "=") { return }
    $name, $value = $_ -split "=", 2
    $name = $name.Trim(); $value = $value.Trim().Trim('"')
    if ($name -match "KEY|SECRET|TOKEN|PASSWORD|PWD" -or $value -match '://[^:/@\s]+:[^@/\s]+@') {
        "  {0,-32} <set: {1}>  {2}" -f $name, [bool]$value, (Mask $value)
    } elseif ($value -match "[\\/]") {
        $reachable = try { Test-Path -LiteralPath $value -EA Stop } catch { "n/a" }
        "  {0,-32} {1}  reachable={2}" -f $name, $value, $reachable
    }
}

""
"=== where the resolver actually looks ==="
# bootstrap first: standalone, the vendored style package is off the path and every
# import below dies with a misleading ModuleNotFoundError.
$probe = @'
import os, sys
sys.path.insert(0, "."); sys.path.insert(0, "src")
import haver_chart.bootstrap  # noqa: F401
import resolve as R
for name in ("CLARIFIED_DIR", "LEARNED_PATH", "LEGEND_PATH", "STORE_DIR"):
    val = getattr(R, name, None)
    if val is not None:
        print(f"  {name:<16} {val}  exists={os.path.exists(str(val))}")
'@
$tmp = Join-Path $env:TEMP "_store_probe.py"
$probe | Out-File -Encoding ascii $tmp
& "C:\Users\madz\envs\haver-chart\Scripts\python.exe" $tmp 2>&1 | Select-Object -First 20
Remove-Item $tmp -Force -EA SilentlyContinue

""
"=== store contents ==="
Get-ChildItem "C:\Users\madz\Work\asingh\haver-chart\knowledge\*.json" -EA SilentlyContinue |
    ForEach-Object { "  {0,-44} {1,7} bytes  {2}" -f $_.Name, $_.Length, $_.LastWriteTime }
