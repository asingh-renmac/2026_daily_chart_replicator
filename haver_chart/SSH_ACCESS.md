# Shell access to the host, through the tunnel

Getting a real SSH session from a laptop to the Windows host that serves
`chart.hvr-mcp.work`, so deployments (`git pull`, selftest, restart) stop requiring an
RDP session and a human typing.

**No inbound port is opened.** The host already dials out to Cloudflare for the chart
endpoint; this rides the same tunnel. That matters because `HANDOFF.md` is explicit that
8100/8101 must not be opened on the NSG, and the reasoning applies at least as strongly
to port 22.

There is one unavoidable bootstrap: you cannot configure remote access remotely. Section
2 is done once, at the console or over RDP.

---

## 1. Laptop side — already done (2026-09-05)

| Thing | State |
|---|---|
| `cloudflared` | installed, 2026.8.3 |
| Key pair | `~/.ssh/id_avd`, ed25519, no passphrase |
| `~/.ssh/config` | `Host avd` block added, with the `ProxyCommand` |

The public key to install on the host:

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEyT0JysSsFRWQ6zLmB9PeaM2sG8R82wkFU20hkMjHZL asingh-laptop -> haver AVD
```

The config block, for reference:

```
Host avd
    HostName ssh.hvr-mcp.work
    User madz
    IdentityFile C:\Users\asingh\.ssh\id_avd
    IdentitiesOnly yes
    ProxyCommand cloudflared access ssh --hostname %h
```

`ProxyCommand` is what makes this work without an open port: `ssh` speaks to a local
`cloudflared` process, which reaches the host through the existing outbound tunnel.

---

## 2. Host side — once, over RDP

### 2a. Turn on the SSH server

PowerShell **as Administrator**:

```powershell
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic
```

Make PowerShell the shell SSH hands you, rather than `cmd`:

```powershell
New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell `
  -Value "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" `
  -PropertyType String -Force
```

### 2b. Install the public key — mind the admin rule

**This is the step that silently fails.** Windows OpenSSH ignores
`~\.ssh\authorized_keys` for any account in the Administrators group, and reads
`C:\ProgramData\ssh\administrators_authorized_keys` instead — which must also have its
ACL locked to SYSTEM and Administrators or `sshd` refuses it without saying so. A key
placed in the "obvious" location for an admin account produces a password prompt and no
explanation.

If `madz` is an administrator:

```powershell
$key = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEyT0JysSsFRWQ6zLmB9PeaM2sG8R82wkFU20hkMjHZL asingh-laptop'
Add-Content -Path C:\ProgramData\ssh\administrators_authorized_keys -Value $key
icacls C:\ProgramData\ssh\administrators_authorized_keys /inheritance:r
icacls C:\ProgramData\ssh\administrators_authorized_keys /grant "SYSTEM:F" "Administrators:F"
```

If `madz` is a standard user, the normal location applies instead:

```powershell
New-Item -ItemType Directory -Force -Path $env:USERPROFILE\.ssh | Out-Null
Add-Content -Path $env:USERPROFILE\.ssh\authorized_keys -Value $key
```

Not sure which? `(New-Object Security.Principal.WindowsPrincipal(
[Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole('Administrators')`.

### 2c. Route SSH through the tunnel that already exists

Do **not** create a second tunnel. Add a hostname to the one that is running.

```powershell
cloudflared tunnel route dns haver-chart-avd ssh.hvr-mcp.work
```

Then edit `%USERPROFILE%\.cloudflared\config.yml` and add the SSH rule **above** the
catch-all — ingress rules match in order, and anything after `http_status:404` is dead:

```yaml
ingress:
  - hostname: chart.hvr-mcp.work
    service: http://127.0.0.1:8100
  - hostname: ssh.hvr-mcp.work
    service: ssh://127.0.0.1:22
  - service: http_status:404
```

Restart `cloudflared`. If it is running in the foreground, **the chart endpoint drops for
those few seconds** — do this when nobody is mid-render.

### 2d. Put Cloudflare Access in front of it

The hostname is not a public TCP port and the server is still key-gated, so this is
defence in depth rather than the only lock — but the chart endpoint sits behind Entra and
the shell should not be weaker than the thing it administers.

In Cloudflare Zero Trust → **Access → Applications → Add → Self-hosted**:

- Application domain: `ssh.hvr-mcp.work`
- Policy: Allow, matching your own identity (the same Entra IdP the chart app uses, or
  email OTP if that is quicker to stand up)

---

## 3. Verify from the laptop

```bash
ssh avd "hostname; whoami"
```

First run opens a browser for the Access policy and caches a short-lived token. Then the
real check — that this reaches the machine actually serving charts:

```bash
ssh avd "python C:\Users\madz\Work\asingh\haver-chart\repo\haver_chart\selftest.py"
```

---

## 4. Letting the host pull from GitHub

The host needs its own credential; **do not copy a personal key onto a shared machine.**
A read-only deploy key is generated on the host, so the private half never moves:

```powershell
ssh-keygen -t ed25519 -f $env:USERPROFILE\.ssh\id_github_deploy -N '""' -C "haver AVD deploy"
type $env:USERPROFILE\.ssh\id_github_deploy.pub
```

Add that public key in GitHub under **the repository** → Settings → Deploy keys, leaving
"Allow write access" unchecked. Repo-scoped and read-only means a compromise of the host
cannot rewrite history or reach any other repository.

Then `%USERPROFILE%\.ssh\config` on the host:

```
Host github.com
    HostName github.com
    User git
    IdentityFile C:\Users\madz\.ssh\id_github_deploy
    IdentitiesOnly yes
```

Verify with `ssh -T git@github.com` — "successfully authenticated, but GitHub does not
provide shell access" is the expected reply.

**Alternative, if you would rather store no key at all:** add `ForwardAgent yes` to the
laptop's `Host avd` block, `ssh-add ~/.ssh/id_renmac_work` before connecting, and the
host borrows the laptop's credential for the length of the session. Nothing persists, but
nothing works unattended either — no scheduled pull, no unattended redeploy.
