# Update the host's checkout to origin/main. Previews by default, writes with -Apply.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File avd_deploy.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File avd_deploy.ps1 -Apply
#
# Two host facts are baked in, both discovered the hard way on 2026-09-06:
#
# safe.directory is passed through GIT_CONFIG_* rather than `git config --global`. The
# checkout belongs to madz while this runs as the deploy account, so git refuses it as
# "dubious ownership" -- and the --global form is ACCEPTED AND THEN IGNORED, because a
# non-interactive `powershell -File` session resolves HOME somewhere that file is not.
# The environment form binds to the process and cannot be missed the same way.
#
# origin must stay an HTTPS URL. GitHub over SSH does not work from this host: both port
# 22 and ssh.github.com:443 accept the TCP connection and then never complete the
# handshake. Deploy keys are SSH-only and are therefore not available here.
#
# This does NOT restart the server and does NOT run selftest.py -- both need DLX, which
# only the interactive owner's account can reach. See DEPLOY.md.
param([switch]$Apply)

$ErrorActionPreference = "Stop"
$repo = "C:\Users\madz\Work\asingh\haver-chart\repo"

$env:GIT_CONFIG_COUNT    = "1"
$env:GIT_CONFIG_KEY_0    = "safe.directory"
$env:GIT_CONFIG_VALUE_0  = ($repo -replace '\\', '/')
$env:GIT_TERMINAL_PROMPT = "0"
Set-Location $repo

"repo        : $repo"
"origin      : " + (git remote get-url origin)
"branch      : " + (git rev-parse --abbrev-ref HEAD)
"at          : " + (git log -1 --format='%h %s')

git fetch -q origin main
"origin/main : " + (git log -1 --format='%h %s' origin/main)

$behind = (git rev-list --count HEAD..origin/main).Trim()
$ahead  = (git rev-list --count origin/main..HEAD).Trim()
"behind      : $behind commit(s)"
"ahead       : $ahead commit(s)   (non-zero means the host has local commits)"

$dirty = @(git status --short)
if ($dirty.Count) {
    ""
    "!! working tree is NOT clean -- someone edited the host directly:"
    $dirty | Select-Object -First 20
}

if ($behind -eq "0" -and -not $dirty.Count) { ""; "nothing to do."; exit 0 }

""
"=== files this update would change ==="
git diff --stat HEAD origin/main | Select-Object -Last 30

if (-not $Apply) {
    ""
    "PREVIEW ONLY. Re-run with -Apply to install."
    exit 0
}

# A snapshot only matters when the host carries something no commit holds. When the tree
# is clean, origin/main is a strictly better record and a branch would be noise.
if ($dirty.Count) {
    $branch = "host-local-" + (Get-Date -Format "yyyyMMdd-HHmmss")
    git checkout -q -b $branch
    git add -A
    git -c user.email="deploy@host" -c user.name="host deploy" commit -q -m "host-local state before deploy"
    "snapshot    : " + (git rev-parse --short HEAD) + " on $branch"
    git checkout -q main
}

git merge -q --ff-only origin/main
""
"=== after ==="
"at          : " + (git log -1 --format='%h %s')
"clean       : " + (@(git status --short).Count -eq 0)
"env intact  : " + (Test-Path "config\.env")

""
"=== smoke test (no DLX) ==="
& "C:\Users\madz\envs\haver-chart\Scripts\python.exe" scripts\avd_smoke.py 2>&1 | Select-Object -Last 8
""
"Still to do, as the interactive owner at RDP: selftest.py, then restart the server."
