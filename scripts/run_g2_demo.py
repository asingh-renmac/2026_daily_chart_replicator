"""
run_g2_demo.py — G2 demo: extract every asset from the notes/ sample set
(loose PNGs + .docx embedded media) and run the palette/style PRE-FILTER,
scoring against the plan.md §1.3 ground truth.

Vision confirmation (the authoritative layer) is deferred until ANTHROPIC_API_KEY
is set; this demo exercises extraction (fail-loud) + the deterministic
pre-filter only.

Usage:  python scripts/run_g2_demo.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from extract_assets import (  # noqa: E402
    extract_docx_images, UnreadableImageError, validate_image,
)
from classify import preclassify, classify  # noqa: E402

# --vision turns on the Opus tie-breaker (needs ANTHROPIC_API_KEY + anthropic pkg).
USE_VISION = "--vision" in sys.argv

NOTES = ROOT / "notes"

# Ground truth from plan.md §1.3. Keyed by a stable label.
# Loose PNGs in notes/:
GT_LOOSE = {
    "sample1_chart1.png": "raw_haver", "sample1_chart2.png": "raw_haver",
    "sample1_chart3.png": "raw_haver",
    "sample2_chart1.png": "raw_haver", "sample2_chart2.png": "raw_haver",
    "sample2_chart3.png": "raw_haver",
    "sample6_chart1.png": "raw_haver", "sample6_chart2.png": "raw_haver",
    "sample6_chart3.png": "raw_haver",
    "sample7_chart1.png": "raw_haver", "sample7_chart2.png": "raw_haver",
    "sample7_chart3.png": "raw_haver",
    "sample9_chart1.png": "raw_haver", "sample9_chart2.png": "raw_haver",
}
# docx embedded media: (docx, member_name) -> verdict
GT_DOCX = {
    ("sample3.docx", "image1.png"): "skip",       # Job-openings bars (RenMac/Macrobond)
    ("sample3.docx", "image2.png"): "skip",       # 5-line overdone (Macrobond)
    ("sample3.docx", "image3.png"): "raw_haver",  # JOLTS Quits [-4] vs ECI (Haver)
    ("sample4.docx", "image1.png"): "raw_haver",  # ISM Prices/Supplier Del (Haver)
    ("sample4.docx", "image2.png"): "skip",       # ISM PMI model (RenMac maroon)
    ("sample4.docx", "image3.png"): "skip",       # WHAT RESPONDENTS ARE SAYING (text)
    ("sample4.docx", "image4.png"): "skip",       # Inventory investment (Macrobond)
    ("sample5.docx", "image1.png"): "raw_haver",  # Philly Fed z-score (Haver)
    ("sample5.docx", "image2.png"): "raw_haver",  # Philly Fed prices (Haver)
    ("sample8.docx", "image1.png"): "raw_haver",  # Housing units authorized vs starts (Haver)
    ("sample8.docx", "image2.png"): "raw_haver",  # Housing multifamily 2mo MA (Haver)
    ("sample8.docx", "image3.png"): "raw_haver",  # Multi-unit West (Haver)
}


def _verdict(data: bytes) -> tuple[str, str]:
    if USE_VISION:
        v, reason, _ = classify(data, use_vision=True)
        return v, reason
    v, reason, _ = preclassify(data)
    return v, reason


def main() -> int:
    if USE_VISION and not os.environ.get("ANTHROPIC_API_KEY"):
        print("--vision requested but ANTHROPIC_API_KEY is not set (Q7). Aborting.")
        return 2
    mode = "VISION tie-breaker" if USE_VISION else "palette/style PRE-FILTER only"
    print(f"Mode: {mode}")
    rows = []   # (label, truth, verdict, ok, reason)
    n_extracted = 0

    # Loose PNGs
    for name, truth in GT_LOOSE.items():
        p = NOTES / name
        data = p.read_bytes()
        try:
            validate_image(data, name=name, source="loose_png", origin="notes/")
            n_extracted += 1
        except UnreadableImageError as e:
            rows.append((name, truth, "UNREADABLE→Teams", False, str(e)))
            continue
        verdict, reason = _verdict(data)
        rows.append((name, truth, verdict, verdict == truth, reason))

    # docx embedded media
    for docx_name in ("sample3.docx", "sample4.docx", "sample5.docx", "sample8.docx"):
        imgs = extract_docx_images(NOTES / docx_name)   # raises loudly if unreadable
        n_extracted += len(imgs)
        for img in imgs:
            key = (docx_name, img.name)
            truth = GT_DOCX.get(key, "?")
            verdict, reason = _verdict(img.data)
            label = f"{docx_name}:{img.name}"
            rows.append((label, truth, verdict, verdict == truth, reason))

    # Report
    print(f"\nExtracted + validated {n_extracted} images (fail-loud on bad formats).\n")
    print(f"{'asset':32} {'truth':10} {'verdict':10} {'ok':3} reason")
    print("-" * 110)
    correct = 0
    for label, truth, verdict, ok, reason in rows:
        correct += int(ok)
        print(f"{label:32} {truth:10} {verdict:10} {'OK' if ok else 'XX':3} {reason}")
    print("-" * 110)
    print(f"\nPre-filter accuracy: {correct}/{len(rows)} "
          f"({100*correct/len(rows):.0f}%) vs §1.3 ground truth.")
    misses = [(l, t, v) for (l, t, v, ok, _) in rows if not ok]
    if misses:
        print("\nMisclassifications (would be caught by the vision tie-breaker):")
        for l, t, v in misses:
            print(f"  {l}: truth={t} prefilter={v}")
    return 0 if not misses else 1


if __name__ == "__main__":
    raise SystemExit(main())
