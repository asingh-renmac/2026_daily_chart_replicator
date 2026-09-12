# Updating a running host (AVD or dedicated VM)

For pushing a code change to a host that is already serving `chart.hvr-mcp.work`.
First-time setup is `SERVER_SETUP.md`; this is the *update* path.

Written for the §15 rollout, which was carried out on 2026-09-06 and is recorded here as
worked rather than as planned — several of the obvious instructions do not survive
contact with this host.

---

## What a remote shell can and cannot do

Shell access is `SSH_ACCESS.md`, through the tunnel that already serves
`chart.hvr-mcp.work`. It runs as a **local** account (`mcpdeploy`), and that boundary
decides how the work splits:

| | over SSH | needs the owner at RDP |
|---|---|---|
| `git pull`, file inspection | yes | |
| knowledge-store edits | yes | |
| transform/chat-store checks (`avd_smoke.py`) | yes | |
| `selftest.py` | | **yes** — talks to DLX |
| restarting the servers | | **yes** — they live in the owner's session |

The deploy account **cannot reach DLX**, and this is not a fixable oversight:

- Haver's path auto-detection reads *per-user* DLX configuration, which lives in the
  owner's profile. It reports `(path:auto) Automatic detection of the database path
  failed`.
- The data share is a domain resource and a local account has no credential for it.
- The failure is a **hang, not an error**. An un-credentialed DLX path raises a GUI login
  modal (see the G10b finding), and nothing in an SSH session can dismiss it. A probe sat
  for 180 seconds and left a stray process.

Giving the deploy account DLX access would mean a second DLX identity, which is a hard
stop pending written confirmation from Haver. Do not go around it.

> **Never kill Python by name on this host.** Six `python.exe` processes belong to the
> owner and three of them are the live servers. `scripts/avd_ps_list.ps1` lists PIDs with
> their owners; kill by PID only.

---

## 1. The host is a git checkout (done 2026-09-06)

It previously held an extracted teammate zip. It is now a checkout of `main`, with the
pre-existing files preserved on a `host-pre-deploy` branch (`c3c2153`) in case anything
was ever needed back. The layout is unchanged: `repo/` is the working tree and the
`vendor/` sibling `bootstrap._adopt_vendored_paths` looks for stays where it is.

Two things about git on this host, both non-obvious:

**`origin` must be an HTTPS URL.** GitHub over SSH does not work here — port 22 *and*
`ssh.github.com:443` both accept the TCP connection and then never complete the
handshake, which is what a middlebox that permits the connection but not the protocol
looks like. Deploy keys are an SSH-only mechanism, so they are not an option; do not
spend time generating one.

**`safe.directory` must come from the environment.** The checkout belongs to the owner
while deploys run as `mcpdeploy`, so git rejects it as "dubious ownership". The catch is
that `git config --global --add safe.directory ...` is *accepted and then ignored*: a
non-interactive `powershell -File` session resolves `HOME` somewhere that file is not.
Use `GIT_CONFIG_COUNT` / `GIT_CONFIG_KEY_0` / `GIT_CONFIG_VALUE_0`, which bind to the
process. `scripts/avd_deploy.ps1` already does this.

## 2. Every later update

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\avd_deploy.ps1          # preview
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\avd_deploy.ps1 -Apply   # install
```

Preview prints how far behind the host is and which files change. `-Apply` fast-forwards,
snapshots first if anyone edited the host directly, and finishes with the smoke test.

A `git pull --ff-only origin main` by hand does the same thing, given the environment
above.

## 3. Check what can be checked remotely

```powershell
python scripts\avd_smoke.py
```

Runs every `fixtures/transform_phrases.json` case through the phrase parser and loads the
chat store. Expect **55 passed, 0 failed**. No DLX, so this runs over SSH — it catches
import errors and parser regressions before anyone books an RDP session.

## 4. Check what cannot — as the owner, at RDP

```powershell
python haver_chart\selftest.py
```

Expect **125/125** — 71 before §11 (six G20a checks), §12 (nine §16.2 checks), §13
(thirty-four picker checks, including §16.4's resume path and §16.5's `ui/message`,
descriptor-escaping and SA-ordering fixes) and §14 (five DLX-session
`/health` checks) were added. This talks to DLX, so
it also proves the host's DLX session is alive. A failure here is a reason to stop, not to
restart the server and hope.

## 5. Migrate the knowledge store — **do not run the migration script on the host**

The host keeps its own copy of the knowledge JSON, not the `P:` share (`HANDOFF.md` §4C),
so a change to how a store is KEYED has to reach that copy too. Deploying code alone
leaves the two out of step, and the symptom is silent: labels do not error, they simply
stop being found and charts fall back to generated legends that look plausible.

**But `scripts/migrate_legend_keys.py` cannot do that job here.** Two independent
reasons, either one sufficient:

1. It derives its old→new mapping by replaying `data/ledger_*.csv`, and **the host has no
   ledger files**. It would map nothing, re-file nothing, and report success.
2. It reconstructs each old key by calling *today's* `phrase_to_haver`. Once §15.1b has
   been deployed that mapper has changed, so the reconstruction is no longer faithful —
   it computes old keys that were never used.

The mapping can only be derived faithfully **on the machine that owns the ledger, before
the phrase mapper changes**. So derive it there and install the result:

```powershell
# from the ledger-owning machine, after its own --apply
scp <local>/clarified-knowledge/legend_labels.json avd:C:/Users/mcpdeploy/legend_labels.new.json
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\avd_store_sync.ps1
```

`avd_store_sync.ps1` backs up the host's file, installs the migrated one and verifies the
result. **Justify the swap before running it** — the two stores must be the same store,
differing only by the migration:

- the other three JSON files byte-identical, and
- every differing legend key carrying an *identical* label, `source_descriptor` and
  `added` timestamp, so only the key name moves.

For §15 that held exactly: 7 keys re-filed plus 1 copy, 79 → 80 entries, 72 shared keys
with zero label differences and no label text lost anywhere. If it does not hold, the
host's store is not the one the migration was measured against — stop and compare.

## 6. Carry the chat memory across — the operator slug CHANGES

`chat_store` names its file after the operator. Over stdio on a laptop there is no token,
so it is `chat_learned.local.json`. **On the host it runs behind Entra**, so the slug
comes from the token's username and the file becomes `chat_learned.<username>.json`. A
store built on the laptop is invisible to the server until it is renamed.

> **First: Claude Desktop caches the connector's tool list.** A release that ADDS tools —
> §15 added `remember_binding` and `forget_binding` — will not show them to a client that
> connected before the restart. The client states plainly that the tool does not exist,
> and reasons confidently from the old tool set, so it reads as a server fault rather than
> a stale client.
>
> **Toggle the connector off and on** (Settings → Connectors), then start a new chat. Do
> that FIRST, not last. Quitting and relaunching the app is the intuitive move and it is
> not reliable: measured 2026-09-06, a full quit-and-relaunch plus a new chat still showed
> the old four tools, and an off/on toggle picked up the new ones immediately. The cache
> being invalidated does not live in the window, so closing the window does not clear it.
> Only remove and re-add as a last resort, since that discards the Entra authorization.
>
> **Before touching the client at all, prove the server is actually serving the new tool**,
> or you will toggle and relaunch against a host that never restarted:
>
> ```bash
> scp scripts/avd_verify_running.py avd:C:/Users/mcpdeploy/
> ssh avd 'Set-Location C:\Users\madz\Work\asingh\haver-chart\repo; & "C:\Users\madz\envs\haver-chart\Scripts\python.exe" C:\Users\mcpdeploy\avd_verify_running.py'
> ```
>
> It prints the host's commit, the process start time against `server.py`'s write time
> (a process older than the file means the restart did not take), and the tool list the
> deployed code registers. Two failures look identical from the chat window — code not
> pulled, and code pulled but not loaded — and this separates them without a guess.
>
> Confirm before going further by asking which tools the connector exposes. §15 has four:
> `resolve_series`, `remember_binding`, `forget_binding`, `render_chart`.

`/health` will not tell you the slug: it is unauthenticated, so it always reports the
`local` fallback no matter who is signed in. Ask an authenticated tool instead — from
Claude Desktop against the remote connector, call `forget_binding` with a descriptor that
does not exist:

```
forget_binding("zzz-not-a-real-descriptor")
```

It removes nothing and returns `operator` and `store` — the slug and the exact path the
server reads. **Measured 2026-09-06: `operator` is `asingh`**, so the file is
`chat_learned.asingh.json`. Copy the laptop's store to that name, beside the other
knowledge JSON:

```powershell
cd C:\Users\madz\Work\asingh\haver-chart\knowledge
copy chat_learned.local.json chat_learned.<slug>.json
```

Skipping this is not dangerous — the lane just re-asks parks it has already been told
about — but it throws the answers away.

One wart to know about: the `chat_learned.local.json` left behind is what `/health` keeps
reporting, because that view is always the anonymous operator. Once the real store starts
accumulating answers the two diverge, and `/health` will show a stale count that belongs
to nobody. Delete the `local` copy once the ratchet check in §8 passes; the laptop's
`knowledge_repo` still holds the original.

## 7. Restart the server — owner, at RDP

Restarting is cheap: the lane keeps no state between calls beyond the parquet cache, and
the JWT signing key is fixed in `.env`, so sessions survive (`SERVER_SETUP.md` §8).

Stop the running `server.py`, start it again the same way, and leave `cloudflared` alone
unless it also died — a second tunnel for `chart.hvr-mcp.work` must never be created.

**This lane is now supervised, and the supervisor lives in the other repo.** Since
2026-09-08 the host runs three scheduled tasks defined in `2026_haver_mcp` —
`scripts\avd_lane_supervisor.ps1` and `scripts\avd_health_watch.ps1`, checked out at
`C:\Users\madz\Work\asingh\haver-data\repo`. That means:

- A restart no longer needs a human. `McpLaneEnsure` runs every five minutes as `madz`
  and starts any lane that is down; `McpLaneNightly` recycles all of them at 02:30
  (moved off 03:30 on 2026-09-09 — Windows Update reboots kept landing 03:27-03:35).
  Preferred over a hand restart, because it reproduces the exact launch arguments.
- The launch command line is **recorded in that other repo's `$LANES` table**. Change the
  interpreter path, the script path or the working directory here and you must change it
  there too, or the supervisor will faithfully restart the lane the old way.
- Startup output now lands in `...\logs\haver-chart.out.log` / `.err.log` instead of a
  console window, and the supervisor's own verdict in `...\logs\supervisor.log`.
- `/health` publishes `session_suspect` and `last_pull_finished` so the hourly watchdog
  can see a dead DLX session behind a process that still answers HTTP. A lane that never
  answers at all shows up as a 502 from the tunnel, which is what a stopped `server.py`
  looks like from outside.

To restart by hand anyway:

```powershell
powershell -NoProfile -File C:\Users\madz\Work\asingh\haver-data\repo\scripts\avd_lane_supervisor.ps1 `
           -Action Restart -Lane haver-chart
```

## 8. Verify from outside

```powershell
curl https://chart.hvr-mcp.work/health
```

The response must now include a **`chat_memory`** block. Its absence means the old process
is still serving and the restart did not take.

Then, from Claude Desktop against the remote connector:

- Ask for a chart with a compound transform in words — *"3-month moving average of the
  monthly change"*. Before §15 this silently plotted a 3-month average of the LEVEL. It
  must now produce `movv(diff(X,1),3)`.
- Resolve one parked slot, then start a **new chat** and ask for the same series. It must
  bind without asking. That is the §15.3 ratchet, and the only check that proves the store
  is writable at the path the server actually uses.

---

## Fallback: copy the files

If git cannot authenticate from the host, copy these into the same relative paths and do
steps 3 through 8 unchanged:

```
src\resolve.py
src\build_chart.py
haver_chart\lane.py
haver_chart\server.py
haver_chart\chat_store.py        (new file)
haver_chart\selftest.py
scripts\avd_smoke.py             (step 3)
fixtures\transform_phrases.json  (needed by both)
```

A fallback, not the plan. Every hand copy is a chance to leave the host on a half-version,
and `chat_store.py` being new is exactly the file a copy loop skips.
