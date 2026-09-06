# Updating a running host (AVD or dedicated VM)

For pushing a code change to a host that is already serving `chart.hvr-mcp.work`.
First-time setup is `SERVER_SETUP.md`; this is the *update* path.

Written for the §15 rollout, but the shape is general.

---

## The step that is easy to miss

**The host has its own copy of the knowledge JSON**, not the `P:` share — see
`HANDOFF.md` §4C. So a code change that alters how a store is KEYED needs the store
migrated on that host too. Deploying the code alone leaves the two out of step, and the
symptom is silent: labels do not error, they just stop being found, and charts fall back
to generated legends that look plausible.

§15.1a is exactly this shape. `transform_key` now derives from the slot's formula, so
eight legend entries must be re-filed. That was done on the development machine on
2026-09-04; **the host's copy has not been touched.** Step 4 below is not optional.

Order matters: **code first, then the migration.** `scripts/migrate_legend_keys.py`
reconstructs each old key using the *current* phrase mapper, so it must run against the
new code.

---

## 0. Get a shell on the host first

`haver_chart/SSH_ACCESS.md`. Everything below assumes you can run commands on the host
without an RDP session. That is set up once, through the tunnel that already serves
`chart.hvr-mcp.work`, so no inbound port is opened.

## 1. Convert the host to a git checkout (one time only)

The host currently holds an extracted teammate zip. Copying files by hand for every
change is how a host silently ends up on a mix of two versions, so make it a checkout
once and then `git pull` forever after.

This keeps the existing layout: `repo/` becomes the working tree, and the `vendor/`
sibling that `bootstrap._adopt_vendored_paths` looks for stays exactly where it is.

```powershell
cd C:\Users\madz\Work\asingh\haver-chart\repo
git init
git remote add origin <the repo URL you can authenticate to from this host>
git fetch origin main
git reset --hard origin/main
```

`git reset --hard` rewrites **tracked** files only. `config\.env`, the knowledge JSON and
anything else gitignored or untracked are left alone — which is what makes this safe, and
also what makes it worth confirming before you run it:

```powershell
git status --short          # anything listed as ?? is untracked and will survive
```

**Authentication.** The dev machine uses an SSH alias (`git@github-work:...`) that will
not exist on the host. Either copy an SSH key and matching `~/.ssh/config` entry, or use
an HTTPS URL with a personal access token. If neither is available, skip to the fallback
at the bottom.

## 2. Every later update

```powershell
cd C:\Users\madz\Work\asingh\haver-chart\repo
git pull --ff-only origin main
```

## 3. Check the code before restarting anything

```powershell
python haver_chart\selftest.py
```

Expect **71/71**. This talks to DLX, so it also proves the host's DLX session is alive. A
failure here is a reason to stop, not to restart the server and hope.

## 4. Migrate the knowledge store — only when a release says to

For §15:

```powershell
python scripts\migrate_legend_keys.py            # dry run; read the plan it prints
python scripts\migrate_legend_keys.py --apply    # writes, after a timestamped backup
```

Expect **8 entries re-filed (7 moved, 1 copied)**, store 79 → 80. It is idempotent, so a
second `--apply` is a no-op and re-running after a later pull is harmless. It writes a
`legend_labels.<timestamp>.bak.json` beside the store before touching anything.

If the count differs from 8, the host's store is not the one this was measured against.
Stop and compare rather than applying.

## 4b. Carry the chat memory across — the operator slug CHANGES

`chat_store` names its file after the operator. On a laptop over stdio there is no token,
so the file is `chat_learned.local.json`. **On the host it runs over HTTP behind Entra**,
so the slug comes from the token's username and the file becomes
`chat_learned.<username>.json`. A store built up on the laptop is therefore invisible to
the server unless it is renamed.

The server tells you the name it is looking for — that is what the `chat_memory` block in
`/health` is for:

```powershell
curl https://chart.hvr-mcp.work/health     # read chat_memory.operator and chat_memory.path
```

Then copy the laptop's store to that name, next to the other knowledge JSON:

```powershell
copy chat_learned.local.json chat_learned.<username-from-health>.json
```

Do this AFTER the first restart, since the slug is not knowable until a signed-in request
has been served. Skipping it is not dangerous — the lane simply re-asks parks it has
already been told about — but it throws away the answers.

## 5. Restart the server

Restarting is cheap: the lane keeps no state between calls beyond the parquet cache, and
the JWT signing key is fixed in `.env`, so sessions survive (`SERVER_SETUP.md` §233).

Stop the running `server.py`, start it again the same way, and leave `cloudflared` alone
unless it also died — a second tunnel for `chart.hvr-mcp.work` must never be created.

## 6. Verify from outside

```powershell
curl https://chart.hvr-mcp.work/health
```

For the §15 rollout the response must now include a **`chat_memory`** block. Its absence
means the old process is still serving and the restart did not take.

Then, from Claude Desktop against the remote connector:

- Ask for a chart with a compound transform in words — *"3-month moving average of the
  monthly change"*. Before §15 this silently plotted a 3-month average of the LEVEL. It
  must now produce `movv(diff(X,1),3)`.
- Resolve one parked slot, then start a **new chat** and ask for the same series. It must
  bind without asking. That is the §15.3 ratchet, and it is the only check that proves
  the store is writable at the path the server actually uses.

---

## Fallback: copy the files

If git cannot authenticate from the host, copy these into the same relative paths and do
steps 3 through 6 unchanged. The list is every tracked file §15 touched that the server
loads at runtime:

```
src\resolve.py
src\build_chart.py
haver_chart\lane.py
haver_chart\server.py
haver_chart\chat_store.py        (new file)
haver_chart\selftest.py
scripts\migrate_legend_keys.py   (needed for step 4)
fixtures\transform_phrases.json  (needed by selftest)
```

This is a fallback, not the plan. Every hand copy is a chance to leave the host on a
half-version, and `chat_store.py` being new is exactly the file a copy loop skips.
