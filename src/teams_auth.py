"""
teams_auth.py — delegated Microsoft Graph token for the Teams approval loop.

App-only/client-credentials CANNOT post Teams messages at runtime (verified: the
only app permission is Teamwork.Migrate.All, import-only). So the loop runs as
ME via a DELEGATED token: a one-time interactive (device-code) consent mints a
refresh token; every run after that refreshes silently — headless.

Scopes: ChatMessage.Send, Chat.ReadWrite, User.Read.

HARDENING (the failure mode is "approvals silently stop"):
  * Refresh token stored ENCRYPTED at rest (Fernet, key in econ-templates/config
    /.env as AS_TEAMS_TOKEN_KEY), 0600, never in git — same discipline as the
    Graph creds; it acts as me and reads my chats.
  * Refresh failure ALERTS, never stalls: `get_access_token()` raises
    `TeamsAuthError`; the pipeline calls `alert_reauth_needed()` to email me
    out-of-band via the existing Mail.Send creds, then fails loud.
  * Re-mint is one command (documented): `python src/teams_auth.py consent`.
    A daily run keeps the token rolling so 90-day inactivity won't bite; a RenMac
    password/MFA/conditional-access change eventually will → re-consent.

Azure app registration prerequisites (one-time): a public-client app (or the
existing app with "Allow public client flows" = yes) with DELEGATED
ChatMessage.Send + Chat.ReadWrite + User.Read. Set AS_TEAMS_CLIENT_ID /
AS_TEAMS_TENANT_ID (falls back to AS_MSGRAPH_*).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_CONFIG_DIR = Path("C:/Users/asingh/new_work/econ-templates/config")
_ENV_FILE = _CONFIG_DIR / ".env"

# All four are user-consentable (no admin grant), so Aman self-consents.
# ChatMessage.Read is needed to poll his free-text replies (delegated, same-sender).
SCOPES = ["Chat.ReadWrite", "ChatMessage.Send", "ChatMessage.Read", "User.Read"]


class TeamsAuthError(RuntimeError):
    """Token missing / refresh failed — re-consent required (caller must ALERT)."""


def _load_env() -> None:
    if not _ENV_FILE.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(_ENV_FILE, override=False)
    except ImportError:
        for raw in _ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip("'").strip('"')
            if k and k not in os.environ:
                os.environ[k] = v


_load_env()


def _client_id() -> str:
    return os.environ.get("AS_TEAMS_CLIENT_ID") or os.environ.get("AS_MSGRAPH_CLIENT_ID", "")


def _tenant() -> str:
    return os.environ.get("AS_TEAMS_TENANT_ID") or os.environ.get("AS_MSGRAPH_TENANT_ID", "")


def _cache_path() -> Path:
    return Path(os.environ.get("AS_TEAMS_TOKEN_CACHE", str(_CONFIG_DIR / "teams_token.bin")))


def _fernet():
    from cryptography.fernet import Fernet
    key = os.environ.get("AS_TEAMS_TOKEN_KEY")
    if not key:
        raise TeamsAuthError(
            "AS_TEAMS_TOKEN_KEY not set. Generate one with "
            "`python src/teams_auth.py keygen` and add it to econ-templates/config/.env")
    return Fernet(key.encode() if isinstance(key, str) else key)


def _load_cache():
    import msal
    cache = msal.SerializableTokenCache()
    p = _cache_path()
    if p.exists():
        try:
            cache.deserialize(_fernet().decrypt(p.read_bytes()).decode("utf-8"))
        except Exception as e:
            raise TeamsAuthError(f"could not decrypt token cache {p}: {e}")
    return cache


def _save_cache(cache) -> None:
    if not cache.has_state_changed:
        return
    p = _cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(_fernet().encrypt(cache.serialize().encode("utf-8")))
    try:
        os.chmod(p, 0o600)
    except Exception:
        pass


def _app(cache):
    import msal
    cid = _client_id()
    if not cid:
        raise TeamsAuthError("client id unset (AS_TEAMS_CLIENT_ID / AS_MSGRAPH_CLIENT_ID)")
    return msal.PublicClientApplication(
        cid, authority=f"https://login.microsoftonline.com/{_tenant()}",
        token_cache=cache)


def get_access_token() -> str:
    """Silent refresh using the stored token. Raises TeamsAuthError if no account
    is consented or the refresh token is dead — caller must ALERT, not swallow."""
    cache = _load_cache()
    app = _app(cache)
    accounts = app.get_accounts()
    if not accounts:
        raise TeamsAuthError("no consented account — run `python src/teams_auth.py consent`")
    res = app.acquire_token_silent(SCOPES, account=accounts[0])
    _save_cache(cache)
    if not res or "access_token" not in res:
        why = (res or {}).get("error_description", "refresh token expired/revoked")
        raise TeamsAuthError(f"silent refresh failed — re-consent needed: {why}")
    return res["access_token"]


def interactive_consent() -> None:
    """One-time device-code consent → caches the (encrypted) refresh token. Works
    headless/remote: prints a URL + code to complete in any browser."""
    cache = _load_cache()
    app = _app(cache)
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise TeamsAuthError(f"could not start device flow: {flow}")
    print(flow["message"], flush=True)
    res = app.acquire_token_by_device_flow(flow)
    _save_cache(cache)
    if "access_token" not in res:
        raise TeamsAuthError(f"consent failed: {res.get('error_description')}")
    print("[ok] consent complete — refresh token cached (encrypted, 0600).")


def list_chats() -> None:
    """GET /me/chats and print id + topic + members, so the self-chat's chatId can
    be grabbed for AS_TEAMS_CHAT_ID without Graph Explorer."""
    import httpx
    tok = get_access_token()
    r = httpx.get("https://graph.microsoft.com/v1.0/me/chats?$expand=members&$top=50",
                  headers={"Authorization": f"Bearer {tok}"}, timeout=30)
    r.raise_for_status()
    chats = r.json().get("value", [])
    if not chats:
        print("(no chats found)")
        return
    for c in chats:
        members = ", ".join(m.get("displayName", "?") for m in c.get("members", []))
        topic = c.get("topic") or "(no topic)"
        print(f"  {c.get('chatType','?'):<10} {c['id']}")
        print(f"      topic: {topic}  |  members: {members}")
    print("\nCopy the desired id into econ-templates/config/.env as AS_TEAMS_CHAT_ID.")


def alert_reauth_needed(reason: str) -> None:
    """Out-of-band re-consent alert via the existing Mail.Send app creds, so a dead
    token surfaces as an email instead of charts silently never getting approved."""
    try:
        sys.path.insert(0, "C:/Users/asingh/new_work/econ-templates/scripts")
        import send_via_graph as mailer
        sender = os.environ.get("AS_SENDER_EMAIL")
        to = os.environ.get("AS_OPS_ALERT_EMAIL") or sender
        if not (sender and to):
            print(f"[alert] AS_SENDER_EMAIL unset; cannot email. Reason: {reason}",
                  file=sys.stderr)
            return
        body = (f"<p>The Teams approval token needs re-consent — chart approvals "
                f"are paused until then.</p><p><b>Reason:</b> {reason}</p>"
                f"<p><b>Fix:</b> run <code>python src/teams_auth.py consent</code> "
                f"on the runner.</p>")
        mailer.send_email(sender=sender, recipients=[to],
                          subject="[chart-bot] Teams re-consent required",
                          html_body=body)
        print(f"[alert] re-consent email sent to {to}")
    except Exception as e:
        print(f"[alert] FAILED to send re-consent email: {e}", file=sys.stderr)


def _main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    if cmd == "consent":
        interactive_consent()
        return 0
    if cmd == "check":
        try:
            tok = get_access_token()
            print(f"[ok] token acquired ({len(tok)} chars); refresh path healthy.")
            return 0
        except TeamsAuthError as e:
            print(f"[FAIL] {e}", file=sys.stderr)
            return 1
    if cmd == "keygen":
        from cryptography.fernet import Fernet
        print(Fernet.generate_key().decode())
        return 0
    if cmd == "list-chats":
        list_chats()
        return 0
    print("usage: python src/teams_auth.py {consent|check|keygen|list-chats}",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
