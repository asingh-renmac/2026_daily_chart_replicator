"""
classify.py — decide whether an image is a RAW HAVER chart (PROCESS) or an
already-finished / non-Haver asset (SKIP).

Two layers (plan.md §1.3, Q4):
  1. preclassify()  — cheap, deterministic palette/style pre-filter (Pillow +
     numpy, no API). Exploits the fact that the Haver screenshot palette is
     ONLY navy + light teal (+ an occasional thin red dashed line + gray
     recession bands), whereas finished RenMac/Macrobond charts use maroon
     #621909 and/or green/orange multi-series palettes, and text screenshots
     have almost no colored-line pixels at all.
  2. classify_with_vision() — authoritative confirmation via Opus vision,
     reading the SOURCE ATTRIBUTION text ("Haver Analytics" vs "Renaissance
     Macro Research / Macrobond") and the title style. This is the tie-breaker
     and the final word for anything the pre-filter marks UNSURE.

The pre-filter NEVER turns a real raw-Haver chart into a SKIP on its own:
its only hard SKIPs are positive non-Haver signals (maroon / green / orange /
no-colored-series). Everything else is PROCESS or UNSURE→vision.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass, asdict

import numpy as np
from PIL import Image

# RenMac maroon — the strongest "finished" tell. Haver never uses it.
MAROON_RGB = np.array([98, 25, 11])     # #621909

# Pre-filter thresholds (fraction of all pixels). Tuned on the notes/ set.
T_COLORED_MIN = 0.004    # below → no plotted series → SKIP (only when NOT decisively blue)
T_MAROON = 0.0008        # maroon present → RenMac/Macrobond → SKIP
T_GREEN = 0.0015         # green series → non-Haver palette → SKIP
T_ORANGE = 0.0025        # orange series → non-Haver palette → SKIP
T_DARKTEXT = 0.05        # lots of dark body text → text/table screenshot → SKIP
# A SPARSE Haver chart (thin navy+teal lines on lots of white) can have a colored_frac
# below T_COLORED_MIN yet be ~100% blue — the 2026-07-20 PCE/Unemployment chart measured
# colored=blue=0.0033. Rescue it: a blue signal this small still counts as a plotted
# series IF blue DOMINATES the (little) color present, so it isn't confused with a stray
# blue speck in a text screenshot. Below this floor, blue is treated as noise.
T_BLUE_MIN = 0.0015


@dataclass
class PaletteFeatures:
    colored_frac: float
    maroon_frac: float
    green_frac: float
    orange_frac: float
    blue_frac: float
    red_frac: float
    darktext_frac: float


def _load_rgb(image: "bytes | str | Image.Image", max_px: int = 320) -> np.ndarray:
    if isinstance(image, Image.Image):
        im = image
    elif isinstance(image, (bytes, bytearray)):
        im = Image.open(io.BytesIO(image))
    else:
        im = Image.open(image)
    im = im.convert("RGB")
    w, h = im.size
    scale = max(w, h) / max_px
    if scale > 1:
        im = im.resize((int(w / scale), int(h / scale)))
    return np.asarray(im, dtype=np.int16)


def extract_palette_features(image: "bytes | str | Image.Image") -> PaletteFeatures:
    rgb = _load_rgb(image)
    flat = rgb.reshape(-1, 3)
    n = len(flat)

    # HSV via Pillow for hue bucketing (H,S,V each 0-255).
    hsv = np.asarray(Image.fromarray(rgb.astype("uint8"), "RGB").convert("HSV"),
                     dtype=np.int16).reshape(-1, 3)
    H, S, V = hsv[:, 0], hsv[:, 1], hsv[:, 2]

    colored = (S > 90) & (V > 60) & (V < 245)   # saturated series pixels (not bg/text/gray)
    colored_frac = colored.mean()

    def hue_frac(lo, hi):
        return (colored & (H >= lo) & (H < hi)).mean()

    orange_frac = hue_frac(13, 32)
    green_frac = hue_frac(55, 125)
    blue_frac = hue_frac(135, 185)
    red_frac = ((colored & ((H < 13) | (H >= 245)))).mean()

    maroon_frac = (np.abs(flat - MAROON_RGB).sum(axis=1) < 70).mean()

    # Dark, low-saturation pixels = black/gray body text. Charts have only thin
    # axes + small tick labels (low); text/table screenshots are full of it.
    darktext = (S < 60) & (V < 110)
    darktext_frac = darktext.mean()

    return PaletteFeatures(
        colored_frac=float(colored_frac), maroon_frac=float(maroon_frac),
        green_frac=float(green_frac), orange_frac=float(orange_frac),
        blue_frac=float(blue_frac), red_frac=float(red_frac),
        darktext_frac=float(darktext_frac),
    )


def preclassify(image: "bytes | str | Image.Image") -> tuple[str, str, dict]:
    """Return (verdict, reason, features). verdict ∈ {raw_haver, skip, unsure}.

    Order matters. The only legit HARD skips are POSITIVE non-Haver signals — dense body
    text, and RenMac/Macrobond maroon/green/orange series — so they are checked FIRST
    (a finished multi-color chart usually ALSO has a blue line; the non-Haver color must
    win). Only THEN do we read a Haver blue/teal signal, and only last do we skip for
    "no plotted series". The "no series" floor must never fire on a sparse-but-real Haver
    chart (thin blue lines + whitespace), so a DOMINANT blue signal is rescued before it."""
    f = extract_palette_features(image)
    feats = asdict(f)
    if f.darktext_frac > T_DARKTEXT:
        return "skip", f"dense body text → text/table screenshot (darktext_frac={f.darktext_frac:.4f})", feats
    if f.maroon_frac > T_MAROON:
        return "skip", f"RenMac maroon present (maroon_frac={f.maroon_frac:.4f})", feats
    if f.green_frac > T_GREEN:
        return "skip", f"green series → non-Haver palette (green_frac={f.green_frac:.4f})", feats
    if f.orange_frac > T_ORANGE:
        return "skip", f"orange series → non-Haver palette (orange_frac={f.orange_frac:.4f})", feats
    # Decisive Haver blue/teal palette. `blue_dominant` = the color present is essentially
    # all blue, which lets a SPARSE chart clear the colored_frac floor below (fixes the
    # 2026-07-20 miss) while a stray blue pixel in a text image (blue not dominant, or
    # below T_BLUE_MIN) does not.
    blue_dominant = f.colored_frac > 0 and f.blue_frac >= 0.5 * f.colored_frac
    if f.blue_frac >= T_BLUE_MIN and blue_dominant:
        return "raw_haver", f"Haver blue/teal palette (blue_frac={f.blue_frac:.4f}, dominant)", feats
    if f.colored_frac < T_COLORED_MIN:
        return "skip", f"no plotted series (colored_frac={f.colored_frac:.4f}) → text/non-chart", feats
    if f.blue_frac > 0:
        return "raw_haver", f"Haver blue/teal palette (blue_frac={f.blue_frac:.4f})", feats
    return "unsure", "no decisive palette signal → defer to vision", feats


# -----------------------------------------------------------------------------
# Vision confirmation (deferred until ANTHROPIC_API_KEY is set; Q7)
# -----------------------------------------------------------------------------

# Default Opus model id (Q7: confirm in console; current Opus is 4.8, not 4.7).
DEFAULT_MODEL = "claude-opus-4-8"

_VISION_PROMPT = """You are classifying a single image pulled from an economics \
research email. Decide if it is a RAW HAVER chart screenshot (PROCESS) or \
anything else (SKIP).

RAW HAVER (raw_haver): source line reads ".../Haver Analytics"; title is \
CENTERED in blue/teal and is often the raw formula (e.g. difa%(movv(NMSCNX,3),3)) \
or the full Haver descriptor + a units line; palette is navy + light teal (maybe \
one thin red dashed line); dual axes marked with left/right arrows; %/SCALAR/US$/ \
INDEX axis tags.

SKIP (skip): already-finished RenMac/Macrobond/Bloomberg chart (source reads \
"Renaissance Macro Research" / "Macrobond" / "Bloomberg"; bold BLACK left-aligned \
plain-English title; maroon #621909 or multi-color palette; bottom legend), OR a \
table / text screenshot (no plotted line series).

Return STRICT JSON only:
{"verdict":"raw_haver"|"skip","source_attribution":"<text you read>",\
"title_style":"centered_blue|bold_black_left|none","reason":"<short>"}"""


def classify_with_vision(image_bytes: bytes, model: str | None = None) -> tuple[str, dict]:
    """
    Authoritative classification via Opus vision: reads the source-attribution
    text + title style and returns ('raw_haver'|'skip', detail dict).

    Requires ANTHROPIC_API_KEY in env and the `anthropic` package. Lazy-imports
    so the deterministic pre-filter keeps working without either.
    """
    import base64
    import json

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY not set — vision classifier unavailable (Q7).")
    try:
        import anthropic
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("`anthropic` package not installed (Q7).") from e

    client = anthropic.Anthropic()
    b64 = base64.standard_b64encode(image_bytes).decode()
    resp = client.messages.create(
        model=model or DEFAULT_MODEL,
        max_tokens=400,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64",
                                             "media_type": "image/png", "data": b64}},
                {"type": "text", "text": _VISION_PROMPT},
            ],
        }],
    )
    text = "".join(block.text for block in resp.content if block.type == "text").strip()
    text = text[text.find("{"): text.rfind("}") + 1]  # tolerate fencing
    detail = json.loads(text)
    verdict = detail.get("verdict", "").lower()
    if verdict not in ("raw_haver", "skip"):
        raise ValueError(f"vision returned unexpected verdict: {detail!r}")
    return verdict, detail


def classify(image_bytes: bytes, model: str | None = None,
             use_vision: bool = False) -> tuple[str, str, dict]:
    """Pre-filter, then (optionally) vision-confirm raw_haver/unsure cases."""
    verdict, reason, feats = preclassify(image_bytes)
    if use_vision and verdict in ("raw_haver", "unsure"):
        v, detail = classify_with_vision(image_bytes, model=model)
        return v, f"vision[{detail.get('source_attribution','?')}]: {detail.get('reason','')} (pre-filter={verdict})", feats
    return verdict, reason, feats
