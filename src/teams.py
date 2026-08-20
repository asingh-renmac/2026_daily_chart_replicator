"""
teams.py — the human-approval transport for the Teams round-trips (plan §4e, §5).

Q1 (resolved): **sync-wait, numbered + free-text, post-and-poll, per-day
channel/thread.** Two round-trips share this one mechanism:
  * title/subtitle selection  → numbered options (the g4_desired _var1/_var2 pairs)
  * unresolved-ticker fallback → free-text `code@database` reply

Transport is abstracted so the loop LOGIC can be proven offline (StubTransport,
the G7 low-stakes dry-run) before any live Graph traffic. `GraphTransport` is the
MS Graph adapter; its wire calls are stubbed until the channel id + Graph scopes
are provisioned (no production run until G7).

Posting message bodies and reading threaded replies needs Graph application
permissions (`ChannelMessage.Send` + `ChannelMessage.Read.All`, or the Chat.*
equivalents) — a different, more privileged surface than the Mail.Send creds the
email sender uses. Provision + confirm that before flipping GraphTransport on.
"""

from __future__ import annotations

import html
import json
import os
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

_TICKER_RE = re.compile(r"\b([A-Za-z0-9_]+@[A-Za-z0-9_]+)\b")
_CHOICE_RE = re.compile(r"^\s*(?:option\s*)?#?\s*(\d+)\s*$", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
# A leading routing tag the human echoes back, e.g. "[sample1_chart3] pa413121@usecon".
_LEAD_TAG_RE = re.compile(r"^\s*\[[^\]]+\]\s*")


def _untag(text: str) -> str:
    """Normalize a raw reply into parseable payload text: strip markup, decode HTML
    entities, then drop the leading `[chart_id]` routing tag.

    Every `parse_*` funnels through here, which is why the decode belongs here and
    not only in the transport. `GraphTransport.replies()` decodes only when Graph
    reports `contentType == "html"`, but a body typed as "text" can still arrive
    entity-encoded — that is how "C&I" reached the 2026-08-03 ledger as "C&amp;I"
    and rendered literally. Normalizing at the parser boundary makes it transport-
    independent, so no reply path can smuggle an entity into chart content."""
    return _LEAD_TAG_RE.sub("", _strip_html(text))

GRAPH = "https://graph.microsoft.com/v1.0"
# Marker prepended to every message WE post. In a delegated 1:1/group chat the
# bot posts AS me and I reply AS me — same sender — so we can't tell question
# from answer by author. This tag lets `replies()` skip our own posts.
BOT_TAG = "[renmac-chart-bot]"


def _strip_html(s: str) -> str:
    """Graph returns chat bodies as HTML, so every reply arrives ENTITY-ENCODED:
    a typed "C&I" comes back as "C&amp;I". Stripping tags alone left those
    entities literal and they rode all the way onto the canvas (2026-08-03: a
    title rendered "Demand for C&amp;I credit keeps firming"). Unescape AFTER
    the tag strip — that way real markup is removed first and text the human
    escaped on purpose (e.g. a literal "<") survives as text."""
    txt = html.unescape(_TAG_RE.sub("", s or ""))
    return txt.replace("\u00a0", " ").strip()


# ─────────────────────────── transport interface ───────────────────────────
class Transport(ABC):
    """Post a question and read the human's later replies. The cursor is OPAQUE:
    `post()` returns a token; `replies(thread, after_token)` returns only replies
    that arrived after it. This avoids any reliance on wall-clock ordering and is
    the basis for the no-duplicate-post / cross-run-resume behaviour (G7)."""

    @abstractmethod
    def post(self, thread_key: str, text: str) -> str:
        """Post `text`; return an opaque cursor marking this message."""

    @abstractmethod
    def replies(self, thread_key: str, after_token: Optional[str]) -> list[str]:
        """Return reply texts (ours excluded) that arrived after `after_token`."""

    def global_replies(self, after_token: Optional[str]) -> list[str]:
        """Every non-bot reply after `after_token`, IGNORING per-chart tags — for the
        chart-agnostic `approve all` token. Default returns []; transports that can
        see the whole surface override this."""
        return []


class StubTransport(Transport):
    """File-backed fake chat for offline proving. Ordering is a monotonic seq
    (not wall-clock), so the demo is deterministic even sub-millisecond. Replies
    are SCRIPTED via `queue_reply()`."""

    def __init__(self, store: str = "outputs/teams_stub.json"):
        self.path = Path(store)
        self.data = {"threads": {}, "seq": 0}
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
            self.data.setdefault("seq", 0)

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    def _thread(self, key: str) -> dict:
        return self.data["threads"].setdefault(key, {"posts": [], "replies": []})

    def _cursor(self) -> str:
        self.data["seq"] += 1
        return f"{self.data['seq']:010d}"

    def post(self, thread_key: str, text: str) -> str:
        cur = self._cursor()
        self._thread(thread_key)["posts"].append({"cursor": cur, "text": text})
        self._save()
        return cur

    def replies(self, thread_key: str, after_token: Optional[str]) -> list[str]:
        rs = self._thread(thread_key)["replies"]
        out = [(r["cursor"], r["text"]) for r in rs
               if after_token is None or r["cursor"] > after_token]
        out.sort()
        return [t for _, t in out]

    def queue_reply(self, thread_key: str, text: str) -> None:  # test helper
        self._thread(thread_key)["replies"].append(
            {"cursor": self._cursor(), "text": text})
        self._save()

    def global_replies(self, after_token: Optional[str]) -> list[str]:
        """All replies across every thread bucket after `after_token` (the flat-chat
        view a live Graph chat has natively). A global token is posted once but can be
        queued to any bucket in tests; merge them all."""
        out = []
        for th in self.data["threads"].values():
            for r in th["replies"]:
                if after_token is None or r["cursor"] > after_token:
                    out.append((r["cursor"], r["text"]))
        out.sort()
        return [t for _, t in out]


class GraphTransport(Transport):
    """Delegated MS Graph adapter targeting a fixed group chat (`AS_TEAMS_CHAT_ID`).
    Token comes from `teams_auth.get_access_token()` (silent refresh); a dead token
    raises TeamsAuthError so the caller can ALERT instead of stalling.

    A Teams CHAT is a flat message list (no channel-style reply threads), so the
    cursor is the posted message's `createdDateTime` and `replies()` returns later
    non-bot messages. `thread_key` is ignored for routing (one chat); the per-day
    grouping is handled by the cursor + ledger state, not separate threads."""

    def __init__(self, chat_id: str = ""):
        # AS_TEAMS_CHAT_ID lives in econ-templates/config/.env alongside the Teams
        # creds. A long-running shell that started before it was added won't have it
        # in os.environ, so load the .env directly (same loader teams_auth uses)
        # instead of relying on shell state.
        if not chat_id and not os.environ.get("AS_TEAMS_CHAT_ID"):
            try:
                import teams_auth  # import side-effect: _load_env() reads the .env
                teams_auth._load_env()
            except Exception:
                pass
        self.chat_id = chat_id or os.environ.get("AS_TEAMS_CHAT_ID", "")

    def _headers(self) -> dict:
        import teams_auth
        return {"Authorization": f"Bearer {teams_auth.get_access_token()}",
                "Content-Type": "application/json"}

    def _chat(self) -> str:
        if not self.chat_id:
            raise RuntimeError("AS_TEAMS_CHAT_ID not set — provide the group chat id")
        return self.chat_id

    @staticmethod
    def _chart_tag(thread_key: str) -> str:
        """The per-chart routing tag `[chart_id]` carried in `thread_key` after
        '::'. In one flat self-chat several charts share the surface, so replies
        are bound to a chart by this tag (the echo-tag) — not by sole-pending,
        which would misroute on a multi-park day."""
        cid = thread_key.split("::")[-1] if "::" in (thread_key or "") else ""
        return f"[{cid}]" if cid else ""

    def post(self, thread_key: str, text: str) -> str:
        import httpx
        body = {"body": {"contentType": "text", "content": f"{BOT_TAG}\n{text}"}}
        r = httpx.post(f"{GRAPH}/chats/{self._chat()}/messages",
                       json=body, headers=self._headers(), timeout=30)
        r.raise_for_status()
        j = r.json()
        return j.get("createdDateTime") or j["id"]

    def _scan(self, after_token: Optional[str], tag: str) -> list[str]:
        import httpx
        r = httpx.get(f"{GRAPH}/chats/{self._chat()}/messages?$top=50",
                      headers=self._headers(), timeout=30)
        r.raise_for_status()
        out = []
        for m in r.json().get("value", []):
            content = (m.get("body") or {}).get("content") or ""
            cdt = m.get("createdDateTime", "")
            if BOT_TAG in content:            # skip our own posts
                continue
            if after_token and cdt <= after_token:
                continue
            text = (_strip_html(content) if (m.get("body") or {}).get(
                "contentType") == "html" else content.strip())
            if not text:
                continue
            if tag and tag not in text:       # addressed to a different chart
                continue
            out.append((cdt, text))
        out.sort()
        return [t for _, t in out]

    def replies(self, thread_key: str, after_token: Optional[str]) -> list[str]:
        return self._scan(after_token, self._chart_tag(thread_key))

    def global_replies(self, after_token: Optional[str]) -> list[str]:
        return self._scan(after_token, "")    # tag-agnostic: the whole chat


# ─────────────────────────── message formatting ────────────────────────────
def format_title_ask(chart_id: str, options: list[dict]) -> str:
    if options:
        lines = [f"[{chart_id}] Pick a title/subtitle (number, free-text your own, "
                 f"or `none` for a title-less chart):"]
        for i, o in enumerate(options, 1):
            sub = f"  — {o['subtitle']}" if o.get("subtitle") else ""
            lines.append(f"  {i}. {o['title']}{sub}")
    else:
        # no commentary to draft from → no proposals; offer custom / title-less directly.
        lines = [f"[{chart_id}] No title drafted (no commentary). Reply "
                 f"`[{chart_id}] title=… subtitle=…` for a custom title, or "
                 f"`[{chart_id}] none` to render it title-less."]
    lines.append(f"  options: `[{chart_id}] 1` / `2` / `title=… subtitle=…"
                 f" [st_force=true]` / `none`")
    lines.append(f"  ↳ start your reply with [{chart_id}] so it routes to this chart. "
                 f"(`none` = no title; a transformed chart still shows its transform "
                 f"label in the subtitle.)")
    return "\n".join(lines)


_MAX_SHOWN_CANDIDATES = 3   # the gate ranks the pool; a real morning needs a shortlist


def _auto_tag(s: dict) -> str:
    """How an auto-bound slot was bound, for the full-picture display."""
    if s.get("bound_via_query") == "(learned)":
        return "auto, learned"
    if s.get("relevance_exact"):
        return "auto, exact"
    sim = s.get("relevance")
    return f"auto, sim {sim}" if sim is not None else "auto"


def _top_candidates(s: dict) -> list[dict]:
    """The TOP-N viable candidates by the gate's descriptor similarity (exact first,
    then sim), each `{code, sim, exact}`. Falls back to bare relevant codes if the
    scored detail isn't present. The FULL pool stays in the ledger, not the ask."""
    detail = s.get("candidate_detail")
    if detail:                                   # already sorted (exact, sim) desc
        return detail[:_MAX_SHOWN_CANDIDATES]
    return [{"code": c} for c in (s.get("candidates") or [])[:_MAX_SHOWN_CANDIDATES]]


def format_series_ask(chart_id: str, slots: list[dict]) -> str:
    """G8b per-series-slot ask — shows the FULL per-chart picture (confirm_all's
    promise: see every bind), not just the parked slots. Auto-bound series are shown
    WITH their resolved ticker so a partially-parked chart isn't split across two
    views; parked series show a TOP-3 ranked shortlist (by the relevance gate's
    similarity), never the full raw pool. The human replies `[chart_id]` then one
    `#<idx> code@database` line per parked item."""
    pending = [s for s in slots if s.get("status") == "pending"]
    n_par, n_tot = len(pending), len(slots)
    lines = [f"[{chart_id}] {n_par} of {n_tot} series need you — full chart below; "
             f"reply `[{chart_id}]` then one `#<n> code@database` line per parked item:"]
    for s in slots:
        idx = s.get("idx")
        desc = s.get("description") or s.get("base_descriptor") or f"series {idx}"
        if s.get("status") == "resolved":                      # auto-bound — show it
            code = s.get("resolved") or "(bound)"
            lines.append(f"  #{idx} {desc}  \u2192  {code}  ({_auto_tag(s)})")
        elif s.get("needs_clarification"):
            phrase = s.get("base_descriptor") or s.get("description") or ""
            lines.append(
                f"  #{idx} CLARIFY: in \u201c{phrase}\u201d, is the moving-average / "
                f"%-change the SERIES NAME or an APPLIED transform? "
                f"reply `#{idx} name` or `#{idx} transform`")
        else:                                                   # parked — needs a ticker
            lines.append(f"  #{idx} {desc}  \u2192  reply `#{idx} code@database`")
            top = _top_candidates(s)
            if top:
                lines.append("        top candidates (by descriptor similarity):")
                for rank, c in enumerate(top, 1):
                    sim = c.get("sim")
                    score = ("exact" if c.get("exact")
                             else f"sim {sim}" if sim is not None else "")
                    suffix = f"  ({score})" if score else ""
                    lines.append(f"        {rank}. {c['code']}{suffix}")
    lines.append("  (or `skip` to drop the whole chart)")
    lines.append(f"  \u21b3 start your reply with [{chart_id}]; tag each line with #<n>.")
    return "\n".join(lines)


def _slot_transform(s: dict) -> str:
    return (s.get("formula") or s.get("applied_transform") or "level")


def format_resolution_summary(chart_id: str, slots: list[dict],
                              chart_spec: Optional[dict] = None) -> str:
    """confirm_all (Part 2): ONE message with a chart's FULL resolved set — every
    series' bound code@db, transform/formula, axis, lag, plus chart-level sample
    range + recession shading. The human ratifies with `approve`, corrects named
    fields, or skips. Nothing renders until this is approved."""
    cs = chart_spec or {}
    lines = [f"[{chart_id}] RESOLVED — review the full set, then reply "
             f"`[{chart_id}] approve` (or correct, or skip):"]
    for pos, s in enumerate(slots, 1):           # 1-based positional numbering
        code = s.get("resolved") or "(unresolved)"
        axis = s.get("axis") or "shared"
        lag = s.get("lag") or "none"
        sim = s.get("relevance")
        rel = ""
        if sim is not None:
            rel = f"  (descriptor {'exact' if s.get('relevance_exact') else f'sim={sim}'})"
        freq = s.get("freq_resolved") or "?"
        adv = "  [freq_hint≠meta]" if s.get("freq_advisory_mismatch") else ""
        desc = s.get("description") or s.get("base_descriptor") or f"series {pos}"
        lines.append(f"  {pos}. {desc}")
        lines.append(f"       \u2192 {code}  |  transform: {_slot_transform(s)}  |  "
                     f"axis: {axis}  |  lag: {lag}  |  freq: {freq}{adv}{rel}")
        # legend label (Opus-proposed / store-confirmed) — ratify with the resolution,
        # override with `legendN=…`. Never a separate round-trip.
        leg = (s.get("proposed_legend") or "").strip()
        src = s.get("legend_source")
        tag = {"store": " (confirmed)", "opus": " (proposed)"}.get(src, "")
        lines.append(f"       legend: {leg or '(pending — reply legend'+str(pos)+'=…)'}{tag}")
    rng = f"{cs.get('sample_start') or '?'}\u2013{cs.get('sample_end_read') or 'derive@pull'}"
    rec = "yes" if cs.get("recession_shading") else "no"
    lines.append(f"  chart: sample {rng}  |  axis_mode: {cs.get('axis_mode', 'shared')}"
                 f"  |  recession shading: {rec}")
    lines.append(f"  reply:  `[{chart_id}] approve`   |   "
                 f"`[{chart_id}] 1=CODE@DB  2:axis=R  3:transform=\u2026  legend1=\u2026`   |   "
                 f"`[{chart_id}] skip`")
    return "\n".join(lines)


def format_ticker_ask(chart_id: str, unresolved: list[str],
                      candidates: Optional[dict] = None) -> str:
    lines = [f"[{chart_id}] Could not resolve these series — reply with "
             "`code@database` for each (or `skip` to drop the chart):"]
    for desc in unresolved:
        lines.append(f"  • {desc}")
        for c in (candidates or {}).get(desc, []):
            lines.append(f"      candidate: {c}")
    lines.append(f"  ↳ start your reply with [{chart_id}] so it routes to this chart.")
    return "\n".join(lines)


# ─────────────────────────── reply parsing ─────────────────────────────────
_APPROVE_ALL_RE = re.compile(r"^approve[\s\-]*(all|\*)$", re.I)


def is_approve_all(text: str) -> bool:
    """True for the CHART-AGNOSTIC bulk token `approve all` / `approve *` (also
    `approve-all`). It must carry NO leading `[chart_id]` tag — a tagged reply is
    chart-scoped and handled by that chart's parser, never as a global sweep."""
    t = _strip_html(text or "").strip()
    if _LEAD_TAG_RE.match(t):                 # `[id] …` → chart-scoped, not global
        return False
    return bool(_APPROVE_ALL_RE.match(t))


def parse_title_choice(replies: list[str], options: list[dict]) -> Optional[dict]:
    """Numbered selection wins; otherwise the first non-empty free-text line
    becomes a custom title (subtitle left to the existing proposal/None)."""
    for text in replies:
        m = _CHOICE_RE.match(_untag(text).strip())
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(options):
                return dict(options[idx])
    for text in replies:
        t = _untag(text).strip()
        if t and not _CHOICE_RE.match(t):
            return {"title": t, "subtitle": ""}
    return None


# explicit subtitle-force override token: `st_force`, `st_force=true`, `st-force=1`, …
_ST_FORCE_RE = re.compile(r"\bst[_\s-]*force\b\s*(?:=\s*(true|false|1|0|yes|no))?", re.I)


def parse_title_reply(replies: list[str], options: list[dict]) -> Optional[dict]:
    """confirm_all title round-trip (Part 3). Superset of parse_title_choice:
      * `approve` / `1`  → the proposed (first) option;
      * `2`              → an alternate by number;
      * `title=…` and/or `subtitle=…` → field overrides (missing field falls back to
        the proposed option's value);
      * any other free text → a custom title.
    """
    proposed = options[0] if options else {"title": "", "subtitle": ""}
    for text in replies:
        t = _untag(text).strip()
        if not t:
            continue
        low = t.lower()
        # `none` / `no-title` → render TITLE-LESS (a title-round-trip choice, not a run
        # mode). The transform-label subtitle is non-suppressible, so a transformed chart
        # still shows e.g. "(3-month annualized % change)"; a level chart shows nothing.
        if low in ("none", "no-title", "no title", "notitle", "no_title"):
            return {"title": "", "subtitle": "", "no_title": True}
        # st_force: an EXPLICIT override — use my subtitle VERBATIM, append no auto
        # transform-label. Parsed + STRIPPED first so it can't leak into the subtitle
        # text (the greedy `subtitle=(.*)` would otherwise swallow it). Absent = default
        # (auto-label appended; the tripwire stays on).
        st_force = False
        sf = _ST_FORCE_RE.search(t)
        if sf:
            st_force = (sf.group(1) or "true").lower() in ("true", "1", "yes")
            t = (t[:sf.start()] + " " + t[sf.end():]).strip()
            low = t.lower()
        # field overrides (may appear together on one line). subtitle uses (.*) so an
        # EMPTY `subtitle=` is a deliberate BLANK (not a no-match → proposed fallback);
        # `none`/`-`/`blank`/`(none)` are sentinels for the same, so a title can render
        # with no subtitle. A missing subtitle= still falls back to the proposed value.
        tm = re.search(r"title\s*=\s*(.+?)(?=\s+subtitle\s*=|$)", t, re.I)
        sm = re.search(r"subtitle\s*=\s*(.*)$", t, re.I)
        if tm or sm:
            if sm:
                raw = sm.group(1).strip()
                subtitle = "" if raw.lower() in ("", "none", "-", "(none)", "blank") else raw
            else:
                subtitle = proposed.get("subtitle", "")
            return {"title": (tm.group(1).strip() if tm else proposed.get("title", "")),
                    "subtitle": subtitle, "st_force": st_force}
        if low == "approve":
            return dict(proposed)
        m = _CHOICE_RE.match(t)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(options):
                return dict(options[idx])
            continue
        return {"title": t, "subtitle": ""}      # custom free-text title
    return None


# resolution-summary correction markers: "1=CODE@DB", "2:axis=R", "3:transform=…"
_CORR_MARK_RE = re.compile(r"(\d+)\s*([:=])\s*")
_CORR_FIELD_RE = re.compile(r"(axis|transform|formula|lag)\s*=\s*(.*)", re.I)
# legend override: "legend2=Retail sales ex gas stations (SA)". A single reply line
# may carry SEVERAL overrides ("legend1=… legend2=…"); each value runs up to the NEXT
# `legendN=` marker (or end of line) so legend1 doesn't greedily swallow legend2's text
# — the 2026-07-15 defect where slot-0's label became "…(y/y change) legend2=MBA …".
_LEGEND_MARK_RE = re.compile(r"legend\s*(\d+)\s*=", re.I)


def parse_resolution_reply(replies: list[str], n_slots: int
                           ) -> tuple[bool, dict, bool]:
    """confirm_all resolution gate (Part 2). Returns (approve, corrections, skip):
      * approve     : a bare `approve` ratifies the full set;
      * corrections : {position(1-based) → {code|axis|transform|formula|lag: value}};
      * skip        : drops the chart.
    Precedence is the caller's: skip > corrections (apply + re-post) > approve."""
    approve = False
    skip = False
    corrections: dict = {}
    for text in replies:
        body = _untag(text)
        for line in body.splitlines():
            s = line.strip()
            if not s:
                continue
            low = s.lower()
            if low == "skip":
                skip = True
                continue
            if low == "approve":
                approve = True
                continue
            # legend override(s): `legendN=<label>` — handled BEFORE the generic pos=… /
            # pos:field= markers so "legend1=" isn't mis-parsed as position 1. Support
            # MULTIPLE per line by slicing each label up to the next `legendN=` marker.
            lmarks = list(_LEGEND_MARK_RE.finditer(s))
            if lmarks:
                for i, lm in enumerate(lmarks):
                    pos = int(lm.group(1))
                    end = lmarks[i + 1].start() if i + 1 < len(lmarks) else len(s)
                    val = s[lm.end():end].strip()
                    if 1 <= pos <= n_slots and val:
                        corrections.setdefault(pos, {})["legend"] = val
                continue
            marks = list(_CORR_MARK_RE.finditer(s))
            for i, m in enumerate(marks):
                pos = int(m.group(1))
                if not (1 <= pos <= n_slots):
                    continue
                sep = m.group(2)
                end = marks[i + 1].start() if i + 1 < len(marks) else len(s)
                chunk = s[m.end():end].strip()
                fld = corrections.setdefault(pos, {})
                if sep == "=":                    # "pos=CODE@DB"
                    mt = _TICKER_RE.search(chunk)
                    if mt:
                        fld["code"] = mt.group(1)
                else:                              # "pos:field=value"
                    fm = _CORR_FIELD_RE.match(chunk)
                    if fm:
                        fld[fm.group(1).lower()] = fm.group(2).strip()
    return approve, corrections, skip


_SLOT_TAG_RE = re.compile(r"^#?\s*(\d+)\s*[.:)\-]?\s+(.*)$")


def parse_series_reply(replies: list[str], pending: list[dict]
                       ) -> tuple[dict, dict, bool]:
    """Parse a per-slot reply for one chart. `pending` is the ordered list of
    PENDING slot dicts (each has `idx` and a `needs_clarification` flag).

    Returns (tickers, clarify, skip):
      * tickers : {idx → [code@db, …]} — codes routed to a ticker slot;
      * clarify : {idx → bool}         — native-MA answer (True = series name);
      * skip    : a `skip` line drops the whole chart.

    Routing: an explicit `#<idx>` prefix wins; otherwise a bare `code@db` fills the
    next pending TICKER slot in order, and a bare name/transform answer fills the
    next pending CLARIFY slot — so the natural "two tickers, line by line" reply
    (the texas case) maps correctly without the human memorising indices."""
    ticker_q = [s["idx"] for s in pending if not s.get("needs_clarification")]
    clarify_q = [s["idx"] for s in pending if s.get("needs_clarification")]
    idxset = {s["idx"] for s in pending}
    tickers: dict = {}
    clarify: dict = {}
    skip = False
    for text in replies:
        for line in _untag(text).splitlines():
            s = line.strip()
            if not s:
                continue
            if s.lower() == "skip":
                skip = True
                continue
            idx, payload = None, s
            m = _SLOT_TAG_RE.match(s)
            if m and int(m.group(1)) in idxset:
                idx, payload = int(m.group(1)), m.group(2).strip()
            mt = _TICKER_RE.search(payload)
            if mt:
                code = mt.group(1)
                if idx is None and ticker_q:
                    idx = ticker_q[0]
                if idx is not None:
                    tickers.setdefault(idx, [])
                    if code not in tickers[idx]:
                        tickers[idx].append(code)
                    if idx in ticker_q:
                        ticker_q.remove(idx)
                continue
            low = payload.lower()
            val = None
            if any(k in low for k in ("series name", "native", "name")):
                val = True
            elif any(k in low for k in ("applied", "transform")):
                val = False
            if val is not None:
                if idx is None and clarify_q:
                    idx = clarify_q[0]
                if idx is not None:
                    clarify[idx] = val
                    if idx in clarify_q:
                        clarify_q.remove(idx)
    return tickers, clarify, skip


def parse_ticker_reply(replies: list[str], unresolved: list[str]
                       ) -> tuple[dict, bool]:
    """Scan the WHOLE thread and return (candidates, skip):
      * `candidates`: desc → ordered list of proposed `code@db` (de-duped). The
        caller validates each via get_series and takes the first that confirms,
        so a wrong code followed by a correct one still resolves (no reliance on
        reply timing — the demo runs sub-millisecond).
      * `skip`: a `skip` line anywhere drops the chart.
    Mapping a code to a series: explicit `description = code@db` / `:` prefix, or
    the sole pending series when only one is unresolved."""
    candidates: dict = {d: [] for d in unresolved}
    skip = False
    for text in replies:
        for line in _untag(text).splitlines():
            s = line.strip()
            if s.lower() == "skip":
                skip = True
                continue
            mt = _TICKER_RE.search(s)
            if not mt:
                continue
            ticker = mt.group(1)
            lhs = re.split(r"[:=]", s, 1)[0].strip().lower()
            match = next((d for d in unresolved
                          if d.lower() in lhs or lhs in d.lower()), None)
            if match is None and len(unresolved) == 1:
                match = unresolved[0]
            if match is not None and ticker not in candidates[match]:
                candidates[match].append(ticker)
    return candidates, skip
