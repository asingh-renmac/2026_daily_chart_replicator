# haver-chart — installing it as a server (plan.md §14)

This is the **remote lane**: one host runs `haver-chart` over HTTPS so Claude can render
charts from a laptop, the web, or a phone that has no Haver DLX of its own.

It is not the teammate setup. If you want the tool inside Claude Desktop on your own
machine, read `TEAMMATE_SETUP.md` instead — that path uses `configure.py`, stdio, and no
network at all. The two differ in three ways that matter:

| | Teammate laptop | This server |
|---|---|---|
| Transport | stdio, spawned by Claude Desktop | Streamable HTTP on loopback, behind a tunnel |
| Where settings come from | the `env` block `configure.py` writes into Claude's config | `repo/config/.env` |
| Who may call it | only that machine's Claude | anyone in the Entra assignment list |

Nothing here changes the stdio path. `HAVER_CHART_HTTP` unset is still the default, so a
zip already on someone's laptop behaves exactly as before.

---

## 0. Before you start

| Requirement | Why |
|---|---|
| Windows host with Haver DLX installed and signed in | the lane pulls real data; there is no substitute |
| Python 3.10+ | the lane's interpreter |
| The read-only catalog URL | catalog search stays dark without it |
| A Cloudflare zone you control | to publish the hostname |
| Rights to register an Entra app | to put a sign-in in front of it |

**The DLX credential expires weekly.** Once it does, the Haver API raises a *graphical*
login window, and on a host with no one watching, that call blocks instead of failing.
Sign in on the host before the expiry rather than after. §14.16 has the detail; §3 below
covers how the server survives it.

## 1. Unpack

Extract the teammate zip on the host. The wrapper folder is inside the archive, so
extract to the parent and let it create its own directory:

```
C:\...\haver-chart\
  configure.py
  repo\                  <- the chart pipeline
  vendor\                <- chart style + catalog query modules
  knowledge\             <- you create this in step 3
```

Do not run `configure.py`. It exists to write a Claude Desktop config for a local stdio
install, and a server has no Claude Desktop. Everything it would do, you do here instead.

## 2. Install the packages

```powershell
py -3.12 -m venv C:\Users\<you>\envs\haver-chart
C:\Users\<you>\envs\haver-chart\Scripts\python.exe -m pip install -r C:\...\haver-chart\repo\haver_chart\requirements.txt
```

`fastmcp[azure]` pulls the auth dependencies. A venv that only has `haver` — one left over
from reconnaissance, say — is not enough.

### 2b. X-13, for `sa()` formulas

A Haver formula can carry `sa(...)`, and the lane runs the seasonal adjustment itself
rather than asking Haver for it. That needs **two halves, and pip supplies only one**:

- `statsmodels` drives the adjustment. It is in `requirements.txt`, so step 2 installed
  it. Called out because it is imported lazily — a host without it starts and serves
  perfectly normally, then fails on the first `sa()` formula and nothing earlier hints
  at it.
- The **X-13ARIMA-SEATS binary** from the US Census Bureau is not a Python package:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\...\haver-chart\repo\scripts\avd_install_x13.ps1 -Apply
```

It downloads a pinned build, verifies its SHA256, unpacks it, copies the shipped
executable to the `x13as.exe` name statsmodels looks for, and sets a User `X13PATH`.
Idempotent — it exits early if the binary is already there. Edit `$Root` inside it if the
host is not laid out like the AVD.

`transforms._X13_CANDIDATES` also hard-codes the two known install paths, so `X13PATH` is
belt-and-braces rather than the only signal. If neither resolves, an `sa()` formula parks
with a message naming what it looked for, instead of rendering something unadjusted and
labelling it adjusted.

Verify both halves at once — `scripts\avd_smoke.py` (step 4) reports a `dependencies` line
and an `X-13 binary` line.

## 3. Knowledge store

```powershell
mkdir C:\...\haver-chart\knowledge
```

Copy the four JSON files from `P:\Public\RenMac_Chart_Knowledge` into it:
`learned_descriptors.json`, `legend_labels.json`, `trusted_tickers.json`,
`native_ma.json`.

`knowledge\` at the package root is a name the lane looks for, so no variable is needed.

**Copy them locally; do not point the server at `P:` directly.** `load_legend()` is
re-read several times per render, and on a share a transient hiccup turns into "legend
label not confirmed" — a wrong-looking chart rather than an error. The trade is that this
copy goes stale when the daily lane republishes, so refresh it when you notice labels
drifting.

## 4. Settings

Create `repo\config\.env`. For a loopback-only server (step 5) this is the whole file:

```
NEON_READONLY_DATABASE_URL=<the read-only URL>
```

That is genuinely all. The three path variables that `TEAMMATE_SETUP.md` step 4 puts in
Claude's config — `ECON_TEMPLATES_CHARTS`, `HAVER_MCP_SERVER`, `CLARIFIED_KNOWLEDGE_DIR` —
are found from the package layout. Set one only to override where it points.

One `.env` covers the catalog too, even though the catalog module looks for its own file
elsewhere: this one is read into the process first, and nothing later overrides a variable
already set.

Then verify, before any network work:

```powershell
C:\Users\<you>\envs\haver-chart\Scripts\python.exe C:\...\haver-chart\repo\haver_chart\selftest.py
```

**186/186 as of 2026-09-12**, with three `(package)` lines in its section 2. This resolves
a real series and renders a real PNG, so it exercises DLX, the catalog, X-13 and
matplotlib on this host. Fix anything here before continuing — a tunnel in front of a
broken lane just moves the error somewhere harder to see.

The count grows as checks are added; treat a *lower* number with failures as the signal,
not the total. **187** on a machine that also has the `econ-templates` checkout, because
one check compares the vendored X-13 wrapper against the canonical copy and skips when it
is absent. A server will not have it, so 186 is the correct full pass there.

Also run the no-DLX smoke test, which is what the update path uses later and which checks
the two things step 2 could leave half-done:

```powershell
C:\Users\<you>\envs\haver-chart\Scripts\python.exe C:\...\haver-chart\repo\scripts\avd_smoke.py
```

```
dependencies     : all pinned versions present
X-13 binary      : C:\...\tools\winx13\x13as
```

A version that does not match `requirements.txt`, or a missing X-13, is named here rather
than three weeks later inside a formula.

`scripts\g10d_http_check.py` is the same idea for the transport: it stands up a throwaway
server on port 8123 with dummy credentials and proves HTTP works on this machine, without
needing Entra or Cloudflare yet. Run it now and a later failure has one fewer cause.

## 5. Going public

Everything to this point is local. Adding the public edge is three things, and doing them
in this order means each failure has a single cause.

### 5a. Entra app registration

In the Azure Portal:

1. **App registrations → New registration.** Single tenant.
2. **Redirect URI (Web):** `<public URL>/auth/callback` — must equal
   `HAVER_CHART_PUBLIC_URL` plus the default callback path, exactly.
3. **Expose an API:** keep the default Application ID URI, add a scope named `read`.
4. **Manifest:** `"requestedAccessTokenVersion": 2`.
5. **Certificates & secrets:** new client secret. Record it now — the portal shows it
   once — and diary the expiry.
6. **Enterprise applications → Properties:** **Assignment required = Yes**. Then
   **Users and groups**, and add only yourself.

That last step is the licence boundary, not merely access control: one name means one
user against one DLX entitlement. Adding a second is gated on G10h.

If the first sign-in says "Need admin approval", the tenant has user consent disabled and
this needs the same one-time admin grant as any other app registration here.

### 5b. Tunnel

The host needs no public IP and no inbound firewall rule; `cloudflared` dials out.

```powershell
winget install --id Cloudflare.cloudflared
cloudflared tunnel login
cloudflared tunnel create haver-chart
cloudflared tunnel route dns haver-chart <hostname>
```

`%USERPROFILE%\.cloudflared\config.yml`:

```yaml
tunnel: <UUID>
credentials-file: C:\Users\<you>\.cloudflared\<UUID>.json

ingress:
  - hostname: <hostname>
    service: http://127.0.0.1:8100
  - service: http_status:404
```

Then `cloudflared tunnel run haver-chart`.

Run it in the foreground unless the Python server is also a service. Installing
`cloudflared` as a Windows service makes the tunnel survive a sign-out, but a
foreground Python server will not, and half a surviving stack is worth nothing.

### 5c. Turn on HTTP mode

Append to `repo\config\.env`:

```
HAVER_CHART_HTTP=1
HAVER_CHART_HTTP_PORT=8100
HAVER_CHART_PUBLIC_URL=https://<hostname>
HAVER_CHART_JWT_SIGNING_KEY=<see below>
HAVER_CHART_AZURE_CLIENT_ID=<application (client) id>
HAVER_CHART_AZURE_TENANT_ID=<directory (tenant) id>
HAVER_CHART_AZURE_CLIENT_SECRET=<the secret>
```

Generate the signing key **once** and keep it fixed, so tokens survive a restart and a
recycle does not send everyone back through the browser:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Start it (live AVD path, recorded 2026-08-30):

```powershell
C:\Users\madz\envs\haver-chart\Scripts\python.exe C:\Users\madz\Work\asingh\haver-chart\repo\haver_chart\server.py
```

Same host, user `madz`. Sibling packages:

| Lane | Package directory | Port | Health |
|---|---|---|---|
| haver-chart | `C:\Users\madz\Work\asingh\haver-chart\repo\haver_chart` | 8100 | `https://chart.hvr-mcp.work/health` |
| haver-data | `C:\Users\madz\Work\asingh\2026_haver_mcp\haver_data` | 8101 | `https://data.hvr-mcp.work/health` |
| macrobond-data | `C:\Users\madz\Work\asingh\2026_macrobond_mcp\macrobond_data` | 8102 | `https://data.mbond-mcp.work/health` |

`cloudflared` is `cloudflared tunnel run haver-chart-avd` (foreground).

It prints `Streamable HTTP on 127.0.0.1:8100`. If a secret is missing it exits and names
every variable it wanted — publishing an unauthenticated endpoint is the one failure that
must not be survivable, so it refuses to start rather than start open.

## 6. Prove it

```powershell
curl.exe https://<hostname>/health
```

Then the same URL from a phone on cellular data, off the corporate network:

```json
{"status":"ok","rendering":false,"last_render_finished":null,
 "session_suspect":false,"last_pull_finished":"2026-09-12T01:49:33+00:00",
 "retention_days":14,
 "chat_memory":{"operator":"local","path":"...","exists":false,"entries":0}}
```

`session_suspect` and `last_pull_finished` are what `McpHealthWatch` (step 7) keys on, by
those exact names — the data lane publishes the same two, so one rule covers both. Their
absence means an older build is running.

`chat_memory.operator` always reads `local` here however you are signed in, because
`/health` is unauthenticated and so always sees the anonymous operator. To find your real
slug, call `forget_binding` on a descriptor that does not exist from an authenticated
client; it removes nothing and returns `operator` and the exact store path.

That is the whole public edge proven without Claude or a sign-in in the picture. If
`/health` answers but the connector later fails, the problem is Entra or the redirect
URI, not the plumbing.

`rendering` and `last_render_finished` exist because a wedged process answers a bare
`200` perfectly happily. If `rendering` has been `true` for longer than a render takes,
the likely cause is an expired DLX credential holding the render lock behind an invisible
login window. The lock times out — `CHART_RENDER_LOCK_TIMEOUT_S`, default 300 — so the
server recovers on its own, but signing in to DLX on the host is the actual fix.

## 7. Supervision and alerting

Steps 1-6 leave you with a lane that works until the first time nobody is looking. Three
scheduled tasks fix that, and **all three live in the `2026_haver_mcp` repo, not this
one** — they supervise every lane on the host, so they belong with neither and were put
with the first. Clone it beside this package before going further.

```powershell
# elevated, once
powershell -NoProfile -File ...\2026_haver_mcp\scripts\avd_lane_supervisor_install.ps1
powershell -NoProfile -File ...\2026_haver_mcp\scripts\avd_health_watch_install.ps1
```

| Task | Runs | Does |
|---|---|---|
| `McpLaneEnsure` | every 5 min | starts any lane that is down |
| `McpLaneNightly` | 02:30 | recycles all lanes |
| `McpHealthWatch` | hourly at :37 | reads each `/health`, alerts on a state change |

**Register them elevated (`/rl HIGHEST`).** The lanes then run at high integrity, and an
unelevated caller cannot read an elevated process's command line even as the same user —
`Win32_Process` returns the row with `CommandLine` null, so every lane reads as DOWN and
Ensure starts a duplicate of each. The supervisor refuses to act when it detects this, but
the refusal is a guard, not a substitute for registering the task correctly.

**02:30 is chosen against the patch schedule, not for tidiness.** Windows Update reboots
on this host landed 03:27-03:35 three months running; anything after 03:00 races them.

### The launch commands are recorded in the other repo

`$LANES` in `avd_lane_supervisor.ps1` holds each lane's interpreter path, script path and
working directory. Change any of them here and you must change them there, or the
supervisor will faithfully restart the lane the old way. This is the single easiest thing
to get wrong on a new host, because nothing fails until the first unattended restart.

### alert_config.json

Neither repo contains it — it holds secrets. Create it in the log directory
(`C:\...\logs\alert_config.json`) by hand:

```json
{
  "slack_webhook": "https://hooks.slack.com/services/...",
  "slack_mention": "U...",
  "graph_tenant_id": "...", "graph_client_id": "...", "graph_client_secret": "...",
  "graph_sender": "you@example.com",
  "email_to": ["you@example.com"]
}
```

`slack_mention` must be the raw member ID, because `<@U...>` is what turns a post into a
notification — a plain `@name` renders as text and notifies nobody, which fails in the
most deceptive way available, since the message looks perfect. The mention is also what
makes Slack email you when you are away, so one webhook covers both channels.

Prove the path works **before** you need it, since otherwise it is only ever exercised by
the outage it exists to announce:

```powershell
powershell -NoProfile -File ...\scripts\avd_lane_supervisor.ps1 -TestAlert
powershell -NoProfile -File ...\scripts\avd_health_watch.ps1 -TestAlert
```

### What each watcher can and cannot see

They overlap less than they look. `McpLaneEnsure` watches for a process that **vanished**
and alerts from its `STARTED` branch — which means "I found this lane dead", as opposed to
`RESTARTED`, which means somebody asked. `McpHealthWatch` watches for a process that
**lies**: one answering HTTP while DLX refuses every pull, which is invisible to a
liveness check and ran for four days undetected in September 2026.

Neither sees everything. The hourly poll cannot see a short outage at all — a crash at
09:27:34 recovered by 09:29:30 falls entirely between two polls — which is exactly why the
five-minute supervisor alerts too. And **a healthy sweep says nothing**, deliberately, so
silence means "nothing detected", never "nothing happened".

### Logs

Lane stdout and stderr go to `...\logs\<lane>.out.log` / `.err.log`, and the supervisor's
own verdict to `supervisor.log`. `Start-Process` truncates on redirect, so the supervisor
archives the previous run's log to a timestamped name at every start and prunes past
`-KeepLogDays` (14). Without that, the nightly recycle means no lane log ever survives a
day — and the restart is exactly correlated with the incidents worth reading about.

## 8. Operating notes

- **Retention.** Rendered PNGs are swept after `CHAT_RETENTION_DAYS` (default 14). Every
  render writes a uniquely-named file, so without the sweep the folder grows forever.
- **Secret expiry.** The Entra client secret expires. Rotating it means editing `.env`
  and restarting; the signing key stays put, or every session is invalidated.
- **The stores stay read-only.** `seal_stores` is unchanged over HTTP — every `save_*`
  raises. Remote callers cannot teach the daily lane anything, by construction.
- **Restarting is cheap.** The lane keeps no state between calls beyond the parquet
  cache. Restart freely.
- **Per-operator memory is never preseeded.** The four shared stores in `knowledge\` are
  read by everyone; `chat_learned.<slug>.json` is created empty on a new operator's first
  saved binding and is private to them. Nobody inherits anybody's chat answers.

### Updating this host afterwards

`DEPLOY.md` is the update path; this file is the build path. The one thing to understand
before reading it is **why updating is split across two accounts**:

| | Deploy account (over SSH) | Interactive owner (at RDP) |
|---|---|---|
| Can do | `git fetch` / `merge --ff-only`, the no-DLX smoke test | `selftest.py`, restart the lane |
| Cannot do | anything needing DLX — the session belongs to the owner | — |

So `scripts\avd_deploy.ps1` updates the checkout and deliberately stops, printing what is
left to do. Two host facts are baked into it, both learned the hard way: `safe.directory`
is passed through `GIT_CONFIG_*` rather than `git config --global`, because the checkout
belongs to the owner while the deploy account runs it and the `--global` form is *accepted
and then ignored* in a non-interactive `powershell -File` session; and `origin` must stay
an HTTPS URL, because SSH to GitHub from this host accepts the TCP connection on both 22
and 443 and then never completes the handshake. That second fact is also why the X-13
wrapper is vendored into `src/` instead of imported from `econ-templates`.
