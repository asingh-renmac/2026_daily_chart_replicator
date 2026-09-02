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

**37/37 with three `(package)` lines in section 2.** This resolves a real series and
renders a real PNG, so it exercises DLX, the catalog and matplotlib on this host. Fix
anything here before continuing — a tunnel in front of a broken lane just moves the error
somewhere harder to see.

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
{"status":"ok","rendering":false,"last_render_finished":null,"retention_days":14}
```

That is the whole public edge proven without Claude or a sign-in in the picture. If
`/health` answers but the connector later fails, the problem is Entra or the redirect
URI, not the plumbing.

`rendering` and `last_render_finished` exist because a wedged process answers a bare
`200` perfectly happily. If `rendering` has been `true` for longer than a render takes,
the likely cause is an expired DLX credential holding the render lock behind an invisible
login window. The lock times out — `CHART_RENDER_LOCK_TIMEOUT_S`, default 300 — so the
server recovers on its own, but signing in to DLX on the host is the actual fix.

## 7. Operating notes

- **Retention.** Rendered PNGs are swept after `CHAT_RETENTION_DAYS` (default 14). Every
  render writes a uniquely-named file, so without the sweep the folder grows forever.
- **Secret expiry.** The Entra client secret expires. Rotating it means editing `.env`
  and restarting; the signing key stays put, or every session is invalidated.
- **The stores stay read-only.** `seal_stores` is unchanged over HTTP — every `save_*`
  raises. Remote callers cannot teach the daily lane anything, by construction.
- **Restarting is cheap.** The lane keeps no state between calls beyond the parquet
  cache. Restart freely.
