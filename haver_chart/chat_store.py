"""Per-operator chat memory for resolved parks (plan.md §15.3).

The problem this closes is a RATCHET, not a bug. `seal_stores` (§13.6) makes the daily
learning stores read-only from chat, for a good reason: a chat bind is ratified by a
human looking at a chart, not through `confirm_all`, and `run_daily` depends on those
files. But the seal left the chat lane with no memory AT ALL, so a park resolved by eye
this morning was re-asked this afternoon and again tomorrow. Parks never amortized, and
the lane felt worse the more it was used.

This adds a store the chat lane OWNS, so §13.6 is untouched: `learned_descriptors.json`
is still unwritable from here. The chat file is layered OVER the daily stores at lookup
time (`lane._resolve_kwargs`), never merged into them. The daily lane never reads it.

Three properties worth stating, because each was a decision:

* **One file per operator** (D3), and within an operator ONE file for all chats and all
  days. The server is a long-running process with no notion of a chat session, so "a new
  chat" opens the same file — which is the entire point. A per-chat file would have
  re-created the ratchet with extra steps.
* **Read-through, not a copy** (D3). Lookup consults this store, then falls through to
  the daily `learned`. Seeding by copy would freeze a snapshot on first use and then
  drift silently from the daily lane.
* **The chat entry WINS on conflict.** That is required by D5's second half: correcting
  a wrong auto-bind is only possible if the correction outranks what the daily store
  says. Scope is this process only, so a wrong chat entry cannot reach `run_daily`.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import resolve as R                       # bootstrap put src/ on sys.path

_SCHEMA = 1
_FALLBACK_OPERATOR = "local"


# ─────────────────────────────── identity (D12) ──────────────────────────────
def _sanitize(text: str) -> str:
    """A filename-safe slug. Anything outside [a-z0-9._-] becomes `-`, because this
    string is concatenated into a path on a shared drive."""
    slug = re.sub(r"[^a-z0-9._-]+", "-", (text or "").strip().lower()).strip("-.")
    return slug[:64]


def operator_identity() -> tuple[str, dict]:
    """`(slug, claims)` for whoever is calling — from the Entra token over HTTP.

    Over HTTP the request is already authenticated (§14.7), so FastMCP is holding a
    verified token and nothing new needs to be trusted. On stdio there is no token and
    no second operator, so the slug is a fixed `local`.

    The slug prefers a HUMAN-READABLE claim so that listing the directory tells you
    whose file is whose; the immutable subject is recorded INSIDE the file rather than
    in its name. The trade is deliberate: changing your UPN starts a fresh file and
    re-asks some parks, which is recoverable, whereas an opaque GUID filename makes the
    store unauditable by the person who owns it. `sub` is the fallback so an operator
    with no username claim still gets a private file rather than sharing `local`.
    """
    try:
        from fastmcp.server.dependencies import get_access_token
        token = get_access_token()
    except Exception:
        token = None                      # stdio, or called outside a request
    if token is None:
        return _FALLBACK_OPERATOR, {}

    claims = dict(getattr(token, "claims", None) or {})
    subject = getattr(token, "subject", None) or claims.get("sub") or ""
    readable = (claims.get("preferred_username") or claims.get("upn")
                or claims.get("email") or claims.get("unique_name") or "")
    slug = _sanitize(readable.split("@")[0]) or _sanitize(subject) or _FALLBACK_OPERATOR
    return slug, {"subject": subject, "name": claims.get("name") or "",
                  "username": readable, "oid": claims.get("oid") or ""}


def store_path(operator: Optional[str] = None) -> Path:
    slug = operator or operator_identity()[0]
    return Path(R.CLARIFIED_DIR) / f"chat_learned.{slug}.json"


# ──────────────────────────────── read / write ───────────────────────────────
def _read(path: Path) -> dict:
    """The whole envelope, or an empty one. A store that will not parse must never take
    the lane down — an unreadable memory degrades to no memory, which is exactly the
    behaviour before §15.3."""
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"_schema": _SCHEMA, "_operator": {}, "entries": {}}
    if not isinstance(blob, dict):
        return {"_schema": _SCHEMA, "_operator": {}, "entries": {}}
    blob.setdefault("entries", {})
    return blob


def _write_atomic(path: Path, blob: dict) -> None:
    """Write through a temp file in the SAME directory, then `os.replace`.

    Two chats can be open at once and this file lives on a shared drive, so a partial
    write is a real possibility rather than a theoretical one. `os.replace` is atomic on
    Windows and POSIX alike, so a reader sees either the old file or the new one — never
    a truncated one. Same directory because `replace` across volumes is not atomic.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(blob, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass                          # already replaced: the normal path


def load(operator: Optional[str] = None) -> dict:
    """`{normalized description → {code, descriptor, added, ...}}`.

    Shaped exactly like `resolve.load_learned` so it drops into the same `learned=`
    keyword with no adapter. `resolve.resolve_slot` runs its `double_transform_reason`
    guard over learned entries too, so a chat entry gets the same defense-in-depth as a
    daily one — a remembered bind is a shortcut, not an exemption.
    """
    return _read(store_path(operator)).get("entries") or {}


def remember(description: str, code_at_db: str, *, source: str = "park_resolution",
             note: str = "", operator: Optional[str] = None) -> dict:
    """Record `description → code@db` for this operator. Returns the written entry.

    The caller is responsible for the code being HUMAN-CHOSEN; `lane.remember_binding`
    DLX-confirms it first so a hallucinated ticker cannot enter the store. That guard
    proves the series EXISTS, not that it is the right one — only the operator can say
    that, which is why `forget` (D4) exists.
    """
    slug = operator or operator_identity()[0]
    path = store_path(slug)
    blob = _read(path)
    blob["_schema"] = _SCHEMA
    if not blob.get("_operator"):
        blob["_operator"] = operator_identity()[1]
    entry = {"code": code_at_db, "descriptor": description,
             "added": datetime.now(timezone.utc).isoformat(),
             "source": source, "note": note}
    blob["entries"][R._norm_key(description)] = entry
    _write_atomic(path, blob)
    return entry


def forget(description: str, operator: Optional[str] = None) -> bool:
    """Drop one entry (D4). True when something was removed.

    A wrong binding has to be revocable without hand-editing JSON on a network share —
    otherwise the operator's only remedy for a bad memory is to stop trusting the whole
    store."""
    path = store_path(operator)
    blob = _read(path)
    if blob["entries"].pop(R._norm_key(description), None) is None:
        return False
    _write_atomic(path, blob)
    return True


def describe(operator: Optional[str] = None) -> dict:
    """What this operator's memory currently holds — for `health` and for the operator
    to audit without opening the file."""
    slug = operator or operator_identity()[0]
    path = store_path(slug)
    entries = _read(path).get("entries") or {}
    return {"operator": slug, "path": str(path), "exists": path.exists(),
            "entries": len(entries),
            "descriptions": sorted(entries)[:20]}
