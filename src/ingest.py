"""
ingest.py — date-scoped Microsoft Graph **Mail.Read** ingestion + asset extraction
+ raw-Haver classification (plan §3.1 "Neil's emails" dependency, Q3).

This is the FRONT HALF of the pipeline hitting a real inbox (vs the notes/ fixtures
G2 used). It:

  1. builds an Eastern-wall-clock day window and converts to UTC via zoneinfo
     (America/New_York → UTC-4 in summer / UTC-5 in winter, automatically — never
     a hard-coded offset, which would clip an hour of the prior evening on a
     DST-period date);
  2. fetches Neil's "for the daily" messages for that day with a SERVER-SIDE
     `$filter` (sender + receivedDateTime window), paging via `@odata.nextLink`
     — not pull-all-then-filter. (The subject regex is refined client-side because
     Graph `$filter` can't regex/`contains` on subject.)
  3. extracts every image (inline data: URIs, file attachments, and .docx
     word/media/*) + commentary text, RESILIENTLY: a single unreadable image
     (EMF/WMF/corrupt) is recorded as an error and routed to Teams — never
     silently dropped, and it never aborts the rest of the batch;
  4. classifies each image raw-Haver (PROCESS) vs SKIP via classify.py.

Auth: app-only client-credentials on the EMAIL app (AS_MSGRAPH_*), the same creds
send_via_graph uses. The app must hold the **Mail.Read** APPLICATION permission
with admin consent; a 403 raises MailAccessError with that guidance (fail loud).
This is a DIFFERENT credential from the delegated Teams token (teams_auth.py).
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
import random
import re
import sys
import time as _time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

from classify import classify  # noqa: E402
from extract_assets import (  # noqa: E402
    UnreadableImageError, _ext_from_name, extract_docx_commentary,
    extract_inline_html_images, validate_image,
)

GRAPH = "https://graph.microsoft.com/v1.0"
EASTERN = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

DEFAULT_SENDER = "ndutta@renmac.com"
# plan §3.1 subject gate: /\bdaily\b/i — applied client-side (after the server-side
# sender + time-window filter). Deliberately BROAD: the sender is already filtered to
# Neil, and the asymmetry favors breadth — an under-match is a SILENT chart-drop (Neil
# titles it just "Daily" → excluded → fewer charts than he sent, no error), whereas an
# over-match is a cheap NO-OP (a non-chart "daily …" email is admitted, the classifier
# finds no raw-Haver chart, nothing seeds). classify is the real gate; don't pre-filter
# here. Catches "Daily", "Daily charts", "for the daily", "RE: for the daily", etc.
SUBJECT_RE = re.compile(r"\bdaily\b", re.I)
DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_IMG_EXTS = {"png", "jpg", "jpeg", "gif", "bmp", "tiff", "webp", "emf", "wmf"}


class MailAccessError(RuntimeError):
    """Graph refused the mailbox read (usually missing Mail.Read app permission)."""


# ───────────────────────────── time window ─────────────────────────────────
def eastern_day_window(date_str: str) -> tuple[str, str]:
    """(start_utc, end_utc) ISO-Z for the Eastern wall-clock calendar day.

    Both bounds are wall-clock midnights localized SEPARATELY in America/New_York,
    so a DST-transition day correctly spans 23h/25h. For 2026-06-25 (EDT, UTC-4):
    `2026-06-25T04:00:00Z` .. `2026-06-26T04:00:00Z`.
    """
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    start_local = datetime.combine(d, time.min, EASTERN)
    end_local = datetime.combine(d + timedelta(days=1), time.min, EASTERN)
    fmt = lambda t: t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    return fmt(start_local), fmt(end_local)


def build_filter(start_utc: str, end_utc: str, sender: str) -> str:
    """Server-side Graph `$filter`: sender + half-open receivedDateTime window.
    datetimeoffset literals are unquoted; the address is single-quoted."""
    return (f"from/emailAddress/address eq '{sender}' and "
            f"receivedDateTime ge {start_utc} and receivedDateTime lt {end_utc}")


# ─────────────────────────────── data model ────────────────────────────────
@dataclass
class Asset:
    email_id: str
    subject: str
    received: str
    source: str          # "inline_html" | "attachment:<name>" | "docx:<file>:<member>"
    name: str
    verdict: str         # "raw_haver" | "skip" | "unsure"
    reason: str
    width: int = 0
    height: int = 0
    data: bytes = b""
    saved_path: str = ""
    sha1: str = ""       # content hash of `data` — thread-scoped quote-back dedup key
    duplicate: bool = False   # a quote-back of an earlier image in the SAME thread
    dup_reason: str = ""


@dataclass
class IngestError:
    email_id: str
    subject: str
    where: str           # which asset/source choked
    detected: str        # detected extension/format (e.g. "emf")
    detail: str


@dataclass
class EmailResult:
    id: str
    subject: str
    received: str
    sender: str
    commentary: str = ""
    assets: list[Asset] = field(default_factory=list)
    errors: list[IngestError] = field(default_factory=list)
    conversation_id: str = ""   # Graph thread id; scopes the quote-back dedup

    @property
    def raw_haver(self) -> list[Asset]:
        # a thread quote-back (duplicate) is never a fresh chart to seed/round-trip
        return [a for a in self.assets
                if a.verdict == "raw_haver" and not a.duplicate]


# ───────────────────────────── graph fetch ─────────────────────────────────
def graph_app_token() -> str:
    from azure.identity import ClientSecretCredential
    for v in ("AS_MSGRAPH_TENANT_ID", "AS_MSGRAPH_CLIENT_ID", "AS_MSGRAPH_CLIENT_SECRET"):
        if not os.environ.get(v):
            raise MailAccessError(f"{v} not set — cannot authenticate to Graph")
    cred = ClientSecretCredential(
        tenant_id=os.environ["AS_MSGRAPH_TENANT_ID"],
        client_id=os.environ["AS_MSGRAPH_CLIENT_ID"],
        client_secret=os.environ["AS_MSGRAPH_CLIENT_SECRET"])
    return cred.get_token("https://graph.microsoft.com/.default").token


# Graph transient server errors + throttling — documented as retryable. A single one
# of these on ONE message's attachments must not abort the whole day's ingest.
_GRAPH_TRANSIENT = {429, 500, 502, 503, 504}
_GRAPH_MAX_RETRIES = 5


def _graph_backoff(attempt: int, retry_after: str | None) -> float:
    """Seconds to wait before retry `attempt` (0-based): honor a `Retry-After` header
    if present, else exponential (1,2,4,8,16s) with jitter, capped at 30s."""
    if retry_after:
        try:
            return min(float(retry_after), 30.0)
        except ValueError:
            pass
    return min(2.0 ** attempt, 30.0) + random.uniform(0.0, 0.5)


def _graph_get(url: str, headers: dict, mailbox: str,
               params: dict | None = None) -> dict:
    """GET with explicit error surfacing + transient-retry. Graph 400s carry the real
    reason in the body; raise_for_status alone hides it — so 4xx (except throttling)
    still fails LOUD immediately. Transient server errors (429/500/502/503/504) and
    network blips are RETRIED with exponential backoff + jitter (honoring `Retry-After`)
    before giving up — a flaky 502 `UnknownError` on one attachments call shouldn't sink
    the run. Auth (401/403) never retries: it's a config problem, not a blip."""
    path = url.split("?")[0]
    last = ""
    for attempt in range(_GRAPH_MAX_RETRIES + 1):
        try:
            r = httpx.get(url, headers=headers, params=params, timeout=120)
        except (httpx.TransportError, httpx.TimeoutException) as e:
            last = f"network error: {type(e).__name__}: {e}"
            if attempt < _GRAPH_MAX_RETRIES:
                wait = _graph_backoff(attempt, None)
                print(f"  [graph] {last} on {path} — retry "
                      f"{attempt + 1}/{_GRAPH_MAX_RETRIES} in {wait:.1f}s", file=sys.stderr)
                _time.sleep(wait)
                continue
            raise RuntimeError(f"Graph unreachable on {path} after "
                               f"{_GRAPH_MAX_RETRIES + 1} tries: {last}")
        if r.status_code in (401, 403):
            raise MailAccessError(
                f"Graph {r.status_code} reading {mailbox}: {r.text[:300]}. The email "
                "app (AS_MSGRAPH_*) needs Mail.Read/Mail.ReadWrite (Application) consent.")
        if r.status_code in _GRAPH_TRANSIENT and attempt < _GRAPH_MAX_RETRIES:
            wait = _graph_backoff(attempt, r.headers.get("Retry-After"))
            print(f"  [graph] transient {r.status_code} on {path} — retry "
                  f"{attempt + 1}/{_GRAPH_MAX_RETRIES} in {wait:.1f}s", file=sys.stderr)
            _time.sleep(wait)
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"Graph {r.status_code} on {path}: {r.text[:400]}")
        return r.json()
    raise RuntimeError(f"Graph {path} exhausted {_GRAPH_MAX_RETRIES + 1} tries: {last}")


def fetch_attachments(mailbox: str, msg_id: str, headers: dict) -> list[dict]:
    """All attachments for one message (fileAttachments carry contentBytes), paged.
    Fetched per-message rather than via $expand — $expand on a filtered collection
    400s and doesn't reliably include contentBytes for larger items (the daily docx)."""
    url = f"{GRAPH}/users/{mailbox}/messages/{msg_id}/attachments"
    out: list[dict] = []
    while url:
        j = _graph_get(url, headers, mailbox)
        out.extend(j.get("value", []))
        url = j.get("@odata.nextLink")
    return out


def fetch_messages(mailbox: str, start_utc: str, end_utc: str,
                   sender: str = DEFAULT_SENDER, token: str | None = None) -> list[dict]:
    """All messages from `sender` in [start,end) for `mailbox`, server-side filtered,
    FULLY paged. NOTE: no `$orderby` — Graph rejects ordering by receivedDateTime
    while filtering on a different property (`from`) with a 400; we sort client-side.
    Attachments are fetched per-message (see fetch_attachments)."""
    token = token or graph_app_token()
    headers = {"Authorization": f"Bearer {token}",
               "Prefer": 'outlook.body-content-type="html"'}
    params = {
        "$filter": build_filter(start_utc, end_utc, sender),
        "$select": ("id,internetMessageId,conversationId,subject,receivedDateTime,"
                    "from,hasAttachments,body"),
        "$top": "50",
    }
    url, raw, first = f"{GRAPH}/users/{mailbox}/messages", [], True
    while url:
        j = _graph_get(url, headers, mailbox, params=(params if first else None))
        raw.extend(j.get("value", []))
        url, first = j.get("@odata.nextLink"), False
    # `/messages` spans ALL mail folders, so one delivered email can appear several
    # times (Inbox + a rule's copy) with DIFFERENT Graph ids. Dedup on the RFC
    # Message-ID (internetMessageId), which is identical across those copies.
    out, seen = [], set()
    for m in raw:
        key = m.get("internetMessageId") or m.get("id")
        if key in seen:
            continue
        seen.add(key)
        out.append(m)
    # ALWAYS fetch — Graph sets hasAttachments=False when the ONLY attachments are
    # INLINE (cid: body images), so gating on it silently drops inline charts (the
    # 2026-06-25 07:32 email embedded image001.png inline with hasAttachments=False).
    for m in out:
        m["attachments"] = fetch_attachments(mailbox, m["id"], headers)
    out.sort(key=lambda m: m.get("receivedDateTime", ""))
    return out


# ─────────────────────────── extraction + classify ─────────────────────────
def _classify_asset(data: bytes, *, email_id, subject, received, source, name,
                    use_vision) -> Asset:
    verdict, reason, _ = classify(data, use_vision=use_vision)
    try:
        with io.BytesIO(data) as bio:
            from PIL import Image
            with Image.open(bio) as im:
                w, h = im.size
    except Exception:
        w = h = 0
    return Asset(email_id=email_id, subject=subject, received=received,
                 source=source, name=name, verdict=verdict, reason=reason,
                 width=w, height=h, data=data,
                 sha1=hashlib.sha1(data).hexdigest())


def _docx_assets(blob: bytes, docx_name: str, *, email_id, subject, received,
                 use_vision, errors: list[IngestError]) -> list[Asset]:
    """word/media/* members, validated PER MEMBER so one EMF/WMF doesn't hide the
    rest. Bad members are recorded as IngestError (routed to Teams), not dropped."""
    out: list[Asset] = []
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except Exception as e:
        errors.append(IngestError(email_id, subject, f"docx:{docx_name}",
                                  "zip", f"unreadable .docx: {e}"))
        return out
    with zf:
        members = sorted(n for n in zf.namelist()
                         if n.startswith("word/media/") and not n.endswith("/"))
        for m in members:
            mem = Path(m).name
            try:
                img = validate_image(zf.read(m), name=mem, source="docx_media",
                                     origin=docx_name)
            except UnreadableImageError as e:
                errors.append(IngestError(email_id, subject,
                                          f"docx:{docx_name}:{mem}",
                                          e.detected_ext, str(e)))
                continue
            out.append(_classify_asset(
                img.data, email_id=email_id, subject=subject, received=received,
                source=f"docx:{docx_name}:{mem}", name=mem, use_vision=use_vision))
    return out


def ingest_message(msg: dict, use_vision: bool = False) -> EmailResult:
    """Extract + classify every image in one Graph message dict (no network).
    Resilient: unreadable images become errors; the rest still process."""
    # Prefer the stable RFC Message-ID so re-runs map to the same ledger rows.
    eid = msg.get("internetMessageId") or msg.get("id", "")
    subject = msg.get("subject", "") or ""
    received = msg.get("receivedDateTime", "") or ""
    sender = (((msg.get("from") or {}).get("emailAddress") or {})
              .get("address", "") or "")
    res = EmailResult(id=eid, subject=subject, received=received, sender=sender,
                      conversation_id=msg.get("conversationId", "") or "")

    # 1) inline HTML data: images
    html = ((msg.get("body") or {}).get("content")) or ""
    try:
        for img in extract_inline_html_images(html, origin=eid):
            res.assets.append(_classify_asset(
                img.data, email_id=eid, subject=subject, received=received,
                source="inline_html", name=img.name, use_vision=use_vision))
    except UnreadableImageError as e:
        res.errors.append(IngestError(eid, subject, "inline_html",
                                      e.detected_ext, str(e)))

    # 2) attachments: .docx (commentary + media) and image files
    for att in msg.get("attachments") or []:
        name = att.get("name", "attachment")
        ctype = (att.get("contentType") or "").lower()
        content = att.get("contentBytes")
        if not content:
            continue
        blob = base64.b64decode(content)
        ext = _ext_from_name(name)
        if ext == "docx" or ctype == DOCX_CT:
            res.commentary += (extract_docx_commentary_bytes(blob) + "\n")
            res.assets.extend(_docx_assets(
                blob, name, email_id=eid, subject=subject, received=received,
                use_vision=use_vision, errors=res.errors))
        elif ctype.startswith("image/") or ext in _IMG_EXTS:
            try:
                img = validate_image(blob, name=name, source="attachment",
                                     origin=eid)
            except UnreadableImageError as e:
                res.errors.append(IngestError(eid, subject, f"attachment:{name}",
                                              e.detected_ext, str(e)))
                continue
            res.assets.append(_classify_asset(
                img.data, email_id=eid, subject=subject, received=received,
                source=f"attachment:{name}", name=name, use_vision=use_vision))

    res.commentary = res.commentary.strip()
    return res


def extract_docx_commentary_bytes(blob: bytes) -> str:
    """extract_docx_commentary but from in-memory bytes (attachment path)."""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tf:
        tf.write(blob)
        tmp = tf.name
    try:
        return extract_docx_commentary(Path(tmp))
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


# ───────────────────────── thread-scoped quote-back dedup ───────────────────
def dedup_threads(results: list[EmailResult]) -> int:
    """Mark reply/forward QUOTE-BACKS so they never seed a duplicate chart.

    A reply-with-addition ("RE: for the daily" quoting the original charts + adding
    a new one) re-delivers the earlier images. Keyed on Graph `conversationId` (the
    thread), an extracted image whose CONTENT-HASH already appeared in an EARLIER
    message of the SAME thread is a quote-back → `duplicate=True` (dropped from
    `raw_haver`, still shown in the ingest report). SCOPED per-thread so a genuinely
    recurring chart in an UNRELATED email/thread is never clobbered.

    Runs at INGEST, before seeding, so a requoted chart never reaches Teams. Exact
    content-hash only (start simple); if a mail client re-encodes a quoted image the
    bytes differ and it slips through — the caller flags that so we add a perceptual/
    descriptor fallback. `results` MUST be in ascending received order (fetch sorts).
    """
    seen: dict[str, dict] = {}     # conversationId → {sha1: "first-seen locator"}
    n = 0
    for er in results:
        thread = er.conversation_id or er.id      # fall back to msg id (its own thread)
        first = seen.setdefault(thread, {})
        for a in er.assets:                        # 1) flag hashes seen EARLIER in-thread
            if a.sha1 and a.sha1 in first:
                a.duplicate = True
                a.dup_reason = f"thread quote-back of {first[a.sha1]}"
                n += 1
        for a in er.assets:                        # 2) record AFTER, so same-message
            if a.sha1 and a.sha1 not in first:     #    twins don't self-mark
                first[a.sha1] = f"{er.received or er.id}:{a.source}"
    return n


# ─────────────────────────────── day driver ────────────────────────────────
def ingest_day(mailbox: str, date_str: str, sender: str = DEFAULT_SENDER,
               use_vision: bool = False, token: str | None = None) -> dict:
    start_utc, end_utc = eastern_day_window(date_str)
    flt = build_filter(start_utc, end_utc, sender)
    msgs = fetch_messages(mailbox, start_utc, end_utc, sender=sender, token=token)
    kept = [m for m in msgs if SUBJECT_RE.search(m.get("subject") or "")]
    results = [ingest_message(m, use_vision=use_vision) for m in kept]
    n_dedup = dedup_threads(results)               # thread-scoped quote-back skip
    return {
        "date": date_str, "window": (start_utc, end_utc), "filter": flt,
        "mailbox": mailbox, "sender": sender,
        "n_fetched": len(msgs), "n_subject_kept": len(kept),
        "n_deduped": n_dedup,
        "results": results,
    }
