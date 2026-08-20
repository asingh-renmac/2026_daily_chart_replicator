"""
extract_assets.py — pull every image (and the commentary text) out of a
"for the daily" email or an attached .docx.

G2 module. Sources covered:
  - .docx embedded media (word/media/*) + commentary text (word/document.xml)
  - email inline images (HTML data: URIs and cid: references)
  - email file attachments (Graph fileAttachment.contentBytes, base64)

DESIGN RULES (from plan.md §C2 / §R6)
-------------------------------------
- FAIL LOUD on a format we can't read. Every extracted image is validated by
  trying to open it with Pillow. If that fails (e.g. EMF/WMF, or a corrupt
  blob), we raise UnreadableImageError carrying the filename + detected
  extension so the caller can route it to Teams ("couldn't read image format
  .emf in <doc>"). We NEVER silently drop an image.
- docx media is read with the stdlib zipfile (a .docx IS a zip) so this module
  has no third-party dependency beyond Pillow.

This module only EXTRACTS + VALIDATES. Classification is in classify.py.
"""

from __future__ import annotations

import base64
import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from PIL import Image, UnidentifiedImageError

# Formats Pillow can decode that we are willing to process downstream.
READABLE_FORMATS = {"PNG", "JPEG", "GIF", "BMP", "TIFF", "WEBP"}


class UnreadableImageError(Exception):
    """An embedded image is a format we cannot read (route to Teams, don't skip)."""

    def __init__(self, name: str, detected_ext: str, source: str, reason: str = ""):
        self.name = name
        self.detected_ext = detected_ext
        self.source = source
        self.reason = reason
        super().__init__(
            f"couldn't read image format '{detected_ext}' in {source} "
            f"(asset '{name}'){': ' + reason if reason else ''}"
        )


@dataclass
class ExtractedImage:
    """One image pulled from an email/docx, validated as readable."""

    name: str            # original member/attachment name
    data: bytes          # raw image bytes
    fmt: str             # Pillow format, e.g. "PNG"
    source: str          # "docx_media" | "inline_html" | "attachment"
    origin: str          # the containing doc/email id (for logging + Teams msgs)
    width: int
    height: int


# -----------------------------------------------------------------------------
# Validation (fail-loud)
# -----------------------------------------------------------------------------

def _ext_from_name(name: str) -> str:
    suffix = Path(name).suffix.lower().lstrip(".")
    return suffix or "?"


def validate_image(data: bytes, name: str, source: str, origin: str) -> ExtractedImage:
    """Open with Pillow to confirm it's a readable raster. Raise loudly if not."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            fmt = (im.format or "").upper()
            w, h = im.size
            im.verify()  # catch truncated/corrupt payloads
    except (UnidentifiedImageError, OSError, ValueError) as e:
        raise UnreadableImageError(name, _ext_from_name(name), origin, str(e)[:120]) from e

    if fmt not in READABLE_FORMATS:
        raise UnreadableImageError(name, fmt or _ext_from_name(name), origin,
                                   "format not in READABLE_FORMATS")
    return ExtractedImage(name=name, data=data, fmt=fmt, source=source,
                          origin=origin, width=w, height=h)


# -----------------------------------------------------------------------------
# .docx extraction (stdlib zipfile)
# -----------------------------------------------------------------------------

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def extract_docx_commentary(docx_path: Path) -> str:
    """Plain-text commentary from word/document.xml (paragraph-joined)."""
    with zipfile.ZipFile(docx_path) as zf:
        try:
            xml = zf.read("word/document.xml")
        except KeyError:
            return ""
    root = ET.fromstring(xml)
    paras = []
    for p in root.iter(f"{_W_NS}p"):
        texts = [t.text for t in p.iter(f"{_W_NS}t") if t.text]
        line = "".join(texts).strip()
        if line:
            paras.append(line)
    return "\n".join(paras)


def extract_docx_images(docx_path: Path) -> list[ExtractedImage]:
    """Every word/media/* image, validated. Raises UnreadableImageError loudly."""
    docx_path = Path(docx_path)
    out: list[ExtractedImage] = []
    with zipfile.ZipFile(docx_path) as zf:
        media = sorted(n for n in zf.namelist()
                       if n.startswith("word/media/") and not n.endswith("/"))
        for member in media:
            data = zf.read(member)
            out.append(validate_image(data, name=Path(member).name,
                                      source="docx_media", origin=docx_path.name))
    return out


def extract_docx(docx_path: Path) -> tuple[str, list[ExtractedImage]]:
    """(commentary_text, images) for a single .docx."""
    return extract_docx_commentary(docx_path), extract_docx_images(docx_path)


# -----------------------------------------------------------------------------
# Email extraction (pure functions over a Graph message dict; no network here)
# -----------------------------------------------------------------------------

_DATA_URI_RE = re.compile(r"data:(image/[\w.+-]+);base64,([A-Za-z0-9+/=\s]+)", re.I)


def extract_inline_html_images(html: str, origin: str) -> list[ExtractedImage]:
    """Inline base64 data: URIs embedded in the HTML body."""
    out: list[ExtractedImage] = []
    for i, m in enumerate(_DATA_URI_RE.finditer(html or "")):
        mime, b64 = m.group(1), re.sub(r"\s+", "", m.group(2))
        try:
            data = base64.b64decode(b64, validate=True)
        except Exception as e:  # noqa: BLE001
            raise UnreadableImageError(f"inline_{i}", mime.split("/")[-1], origin,
                                       f"base64 decode failed: {e}") from e
        out.append(validate_image(data, name=f"inline_{i}.{mime.split('/')[-1]}",
                                  source="inline_html", origin=origin))
    return out


def extract_graph_attachments(attachments: list[dict], origin: str) -> list[ExtractedImage]:
    """Graph fileAttachment list → validated images (non-image attachments skipped)."""
    out: list[ExtractedImage] = []
    for att in attachments or []:
        name = att.get("name", "attachment")
        ctype = (att.get("contentType") or "").lower()
        content = att.get("contentBytes")
        if not content:
            continue
        # Only treat image-ish attachments here; .docx is handled separately.
        if not (ctype.startswith("image/") or _ext_from_name(name) in
                {"png", "jpg", "jpeg", "gif", "bmp", "tiff", "webp", "emf", "wmf"}):
            continue
        data = base64.b64decode(content)
        out.append(validate_image(data, name=name, source="attachment", origin=origin))
    return out
