"""
ledger.py — the per-(email × chart) state machine (plan §7.8, §10).

One CSV row per chart. The pipeline is RESUMABLE and IDEMPOTENT: a row carries
its own status, so a re-run re-reads the CSV, skips `done` rows, and resumes
`awaiting_*` rows where they left off (Q1 — no-reply rows stay parked, never
block forever, never render unapproved).

State machine (ticker resolution PRECEDES title approval, per §4/Q2):

    pending
      ├─(any series unresolved)→ awaiting_ticker ──reply(code@db)──┐
      │                                                            │
      └────────────────────────── resolved ◄──────────────────────┘
                 │
                 ▼
        awaiting_approval ──reply(option # / free text)──► approved
                 │                                            │
                 │ (no reply: stays awaiting_approval)        ▼
                 └──────────────────────────────────────► rendered ──► done

JSON-encoded columns: `unresolved` (list[str descriptions still pending]),
`resolved_ticker` (dict desc→code@db), `title_options` (list[{title,subtitle}]).

G8b adds two JSON columns for the vision→resolve path:
  * `chart_spec`  — the Stage-1 ChartSpec.to_dict() (the vision read);
  * `series`      — the N per-series SLOTS (resolve.build_slots), each carrying
    its own status/resolved/candidates. The chart status is rolled up from the
    slots (Defect 2: RESOLVED only when EVERY series is bound). The legacy
    single-slot `unresolved`/`resolved_ticker` lane is unchanged for back-compat.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Status constants
PENDING = "pending"
AWAITING_TICKER = "awaiting_ticker"
RESOLVED = "resolved"                  # resolver bound every slot (NOT yet ratified)
# confirm_all mode (G8c Part 2/3): nothing renders until the human ratifies.
AWAITING_RESOLUTION = "awaiting_resolution"   # full resolved set posted, await approve
RESOLUTION_APPROVED = "resolution_approved"   # ratified → enters the title round-trip
AWAITING_TITLE = "awaiting_title"             # title proposals posted, await approve
AWAITING_APPROVAL = "awaiting_approval"       # selective_park title gate (preserved)
APPROVED = "approved"
RENDERED = "rendered"
DONE = "done"
SKIPPED = "skipped"

FIELDS = [
    "message_id", "received_date", "received_time", "sender", "subject",
    "release_slug", "chart_index", "chart_id",
    "resolved_ticker", "unresolved", "series_lag",
    "chart_spec", "series",
    "title_options", "proposed_title", "chosen_title", "subtitle", "no_title", "st_force",
    "thread_id", "asked_at", "ask_cursor", "ask_sig", "asset_path", "output_path",
    "status", "processed_at",
]
_JSON_COLS = {"resolved_ticker", "unresolved", "title_options",
              "chart_spec", "series"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key(row: dict) -> tuple:
    return (row.get("message_id", ""), str(row.get("chart_index", "")))


class Ledger:
    """Thin CSV-backed table of chart rows keyed by (message_id, chart_index)."""

    def __init__(self, path: str = "outputs/ledger.csv"):
        self.path = Path(path)
        self.rows: list[dict] = []
        self._index: dict[tuple, dict] = {}
        self.load()

    # ---- persistence --------------------------------------------------------
    def load(self) -> "Ledger":
        self.rows, self._index = [], {}
        if not self.path.exists():
            return self
        with self.path.open("r", encoding="utf-8", newline="") as fh:
            for raw in csv.DictReader(fh):
                row = {k: raw.get(k, "") for k in FIELDS}
                for c in _JSON_COLS:
                    row[c] = json.loads(row[c]) if row.get(c) else None
                self.rows.append(row)
                self._index[_key(row)] = row
        return self

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS)
            w.writeheader()
            for row in self.rows:
                out = dict(row)
                for c in _JSON_COLS:
                    out[c] = json.dumps(row[c]) if row.get(c) is not None else ""
                w.writerow({k: out.get(k, "") for k in FIELDS})

    # ---- access -------------------------------------------------------------
    def upsert(self, **row) -> dict:
        full = {k: "" for k in FIELDS}
        full.update({k: v for k, v in row.items() if k in FIELDS})
        k = _key(full)
        if k in self._index:
            self._index[k].update(full)
            return self._index[k]
        self.rows.append(full)
        self._index[k] = full
        return full

    def get(self, message_id: str, chart_index) -> Optional[dict]:
        """The row for (message_id, chart_index), or None. Used for insert-only
        idempotent seeding (never clobber a row that has already progressed)."""
        return self._index.get((message_id, str(chart_index)))

    def by_status(self, status: str) -> list[dict]:
        return [r for r in self.rows if r.get("status") == status]

    def set_status(self, row: dict, status: str, **updates) -> dict:
        row["status"] = status
        row.update(updates)
        if status == DONE:
            row["processed_at"] = _now()
        return row


# ---- G8b slot helpers -------------------------------------------------------
def slots_resolved_map(slots: list[dict]) -> dict:
    """Resolved slots → {description → bound code@db | formula} for downstream
    render/title use (mirrors the legacy `resolved_ticker` dict)."""
    out = {}
    for s in slots or []:
        if s.get("status") == RESOLVED and s.get("resolved"):
            out[s.get("description") or f"series_{s.get('idx')}"] = s["resolved"]
    return out


def sync_row_from_slots(row: dict) -> dict:
    """Roll the per-series `series` slots up onto the row: set chart status
    (RESOLVED only when every slot is bound — Defect 2), refresh `resolved_ticker`,
    and list still-pending descriptions in `unresolved`."""
    import resolve as R
    slots = row.get("series") or []
    row["resolved_ticker"] = slots_resolved_map(slots)
    row["unresolved"] = [s.get("description") for s in slots
                         if s.get("status") == R.SLOT_PENDING]
    row["status"] = R.chart_status_from_slots(slots)
    return row
