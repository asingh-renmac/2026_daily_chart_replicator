"""
folder_ingest.py — Bloomberg-chat commentary as a SECOND source into the SAME
extract→classify→(G8)→approve→render path (not a new pipeline).

Neil sometimes sends blurbs + raw-Haver charts over Bloomberg chat instead of
email. Aman pastes each commentary's text + charts into a Word doc and drops it
into a dated folder:

    inbox_bloomberg/MMDDYYYY/<anything>.docx

The email ingester yields (commentary, images) per MESSAGE; this yields
(commentary, images) per DOCX. Both return `ingest.EmailResult`, so everything
downstream (seeding, ledger state machine, Teams round-trips, render) is identical.

KEYS / IDEMPOTENCY
  A docx has no Graph message_id, so rows are keyed on a CONTENT HASH of the file
  bytes: `bb:<sha1>`. This is rename-proof (free-form human filenames like
  jobless_claims.docx / ism.docx don't move the key) and edit-aware (a corrected
  doc gets a new hash → re-processes). The `bb:` prefix namespaces it from email's
  `<guid@domain>` internetMessageId in the SAME ledger schema (key is
  (message_id, chart_index)), so the two lanes share one CSV without colliding.

DATE
  Comes from the FOLDER (MMDDYYYY), never the filename. The CLI passes the usual
  YYYY-MM-DD; we map it to the on-disk MMDDYYYY folder.

CLASSIFICATION still runs: every image in the doc goes through the raw-Haver
classifier exactly like an email asset — Bloomberg-native charts, finished RenMac
charts, and tables SKIP; only raw-Haver charts seed. We never blanket-trust a
doc's images as charts-to-rebuild.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from ingest import (  # reuse the hardened docx media + commentary extraction
    EmailResult, _docx_assets, extract_docx_commentary_bytes,
)

ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "inbox_bloomberg"
BB_SENDER = "bloomberg-chat"


def folder_for(date_str: str, root: Path | None = None) -> Path:
    """`inbox_bloomberg/MMDDYYYY` for an Eastern calendar day YYYY-MM-DD."""
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    return (root or INBOX) / d.strftime("%m%d%Y")


def content_key(blob: bytes) -> str:
    """`bb:<sha1>` — content-addressed ledger id (rename-proof, edit-aware)."""
    return "bb:" + hashlib.sha1(blob).hexdigest()


def ingest_docx_file(path: Path, date_str: str, use_vision: bool = False) -> EmailResult:
    """One .docx → one (commentary, images) unit as an EmailResult.

    `id` is the content hash; `subject` is the human filename stem (a useful label
    for the title round-trip); `received` is the folder date; `sender` marks the
    lane. Images flow through `_docx_assets` (per-member validation, EMF/WMF →
    IngestError routed to Teams, never silently dropped)."""
    path = Path(path)
    blob = path.read_bytes()
    eid = content_key(blob)
    label = path.stem
    res = EmailResult(id=eid, subject=label, received=date_str, sender=BB_SENDER)
    res.commentary = extract_docx_commentary_bytes(blob).strip()
    res.assets.extend(_docx_assets(
        blob, path.name, email_id=eid, subject=label, received=date_str,
        use_vision=use_vision, errors=res.errors))
    return res


def ingest_bloomberg_day(date_str: str, root: Path | None = None,
                         use_vision: bool = False) -> dict:
    """All .docx in the dated folder, each as a commentary unit. Returns a day dict
    shaped like `ingest.ingest_day` (so `run_daily._report`/`_seed` are reused),
    tagged `source="bloomberg"`."""
    folder = folder_for(date_str, root=root)
    docs = []
    if folder.exists():
        # Skip Word lock/temp files (~$name.docx) and non-docx noise.
        docs = sorted(p for p in folder.glob("*.docx")
                      if not p.name.startswith("~$"))
    results = [ingest_docx_file(p, date_str, use_vision=use_vision) for p in docs]
    return {
        "date": date_str, "source": "bloomberg", "folder": str(folder),
        "exists": folder.exists(), "n_docs": len(docs),
        "doc_names": [p.name for p in docs], "results": results,
    }
