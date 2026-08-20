"""
propose.py — Opus-drafted title/subtitle options for the title round-trip (§4g).

In production the proposed options the Teams loop offers are LLM-drafted (Opus,
key live per Q7), not hand-written. Each option is a {title, subtitle} pair in
the RenMac/g4_desired register:
  * title    — a takeaway headline, ≤11 words, sentence case, NO colon;
  * subtitle — the parenthetical that carries the transform/units/sample.
Two variants are returned (the _var1/_var2 idea): same takeaway, alternate
subtitle phrasings — so the human picks by number in Teams.

The raw Haver formula NEVER appears here — inputs are the get_series descriptions
and a plain transform label, matching the labeling discipline enforced in
render._assert_clean.
"""

from __future__ import annotations

import json
import os
import re

MODEL = "claude-opus-4-8"  # Q7

_PROMPT = """You are titling a Renaissance Macro Research chart for Neil Dutta's \
daily. Write {n} alternative title+subtitle options.

Chart subject: {subject}
Series plotted: {series}
Transformation applied: {transform}
Frequency / sample: {span}

Rules:
- title: a takeaway HEADLINE (an economic message, not a description), <=11 words,
  sentence case, NO colon, NO ticker/formula syntax.
- subtitle: a short parenthetical-style phrase carrying the transform/units/sample
  (e.g. "3-mo annualized %", "z-score of y/y %"). No formula syntax.
- Keep all {n} titles plausible but distinct in emphasis; vary the subtitle phrasing.

Return ONLY a JSON array of objects with keys "title" and "subtitle". No prose.
"""


def _extract_json_array(text: str) -> list:
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"no JSON array in model output: {text[:200]!r}")
    return json.loads(text[start:end + 1])


def _extract_json_object(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"no JSON object in model output: {text[:200]!r}")
    return json.loads(text[start:end + 1])


# ─────────────────────────── legend labels (grounded) ───────────────────────
# The legend label is a SHORTENING of the real get_series descriptor — Opus
# abbreviates (AHE, CPI, PCE…) but must NOT drop qualifiers that change meaning
# (SA/NSA, index vs level, units, $/hour, base year) and must NOT identify or
# invent (it only sees the verified descriptor, never guesses the series). The
# human ratifies in the resolution round-trip; a subtraction that isn't a clean
# "ex X" is exactly why it routes past a human.
_LABEL_PROMPT = """You are writing a BRIEF chart-legend label from a Haver series' \
official get_series description. Shorten it for a chart legend.

Official description (VERIFIED — this is the only ground truth you have): {descriptor}

Rules:
- Shorten and abbreviate using standard economic abbreviations (AHE = Average Hourly \
Earnings, CPI, PCE, SA, NSA, etc.).
- DO NOT drop qualifiers that change meaning: SA/NSA, index vs level, units ($/hour, \
mil.$), base year (e.g. 2007=100), "ex" exclusions, sector/industry.
- DO NOT identify, reinterpret, or invent — only shorten what the description says.
- Keep it under ~8 words. Keep parenthetical qualifiers like "(SA, $/hr)".

Return ONLY a JSON object: {{"label": "..."}}. No prose.
"""

_COMPOSITE_PROMPT = """You are writing ONE brief chart-legend label for a COMPOSITE \
series computed by an arithmetic operation on Haver series. Ground strictly in the \
official descriptions below; do not invent.

Operation: {operation}
Operands (official get_series descriptions — the only ground truth):
{operands}

Rules:
- Compose ONE brief label (< ~9 words) that matches what the ARITHMETIC actually \
computes. A subtraction A - B is "A excluding B" ONLY if B is a subset of A; if that \
is not clearly true from the descriptions, phrase it literally (e.g. "A less B") — do \
NOT assert a clean "ex" that the math doesn't support.
- Preserve meaning-changing qualifiers (SA/NSA, units, base year).
- Standard abbreviations OK. DO NOT identify or invent beyond the descriptions.

Return ONLY a JSON object: {{"label": "...", "note": "one-line rationale of the phrasing"}}. \
No prose outside the JSON.
"""


def _opus_json_object(prompt: str, max_tokens: int = 300) -> dict:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY not set — cannot propose legend label.")
    import anthropic
    msg = anthropic.Anthropic().messages.create(
        model=MODEL, max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}])
    text = "".join(getattr(b, "text", "") for b in msg.content)
    return _extract_json_object(text)


def propose_series_label(descriptor: str) -> str:
    """Opus-drafted BRIEF legend label from a single series' get_series descriptor.
    Grounded: shortens the verified description, never identifies/invents. Raises if
    the key is unset or the label is empty (fail loud — never ship a fabricated or
    empty legend; the caller parks instead)."""
    if not (descriptor or "").strip():
        raise ValueError("empty descriptor — cannot propose a legend label")
    obj = _opus_json_object(_LABEL_PROMPT.format(descriptor=descriptor.strip()))
    label = (obj.get("label") or "").strip()
    if not label:
        raise ValueError("Opus returned no usable legend label")
    return label


def propose_composite_label(operation: str, operands: list[dict]) -> dict:
    """Opus-drafted label for a composite (e.g. NRS - NRSI7). `operands` is
    [{code, descriptor}, …]. Returns {label, note} — the note flags phrasing risk
    (e.g. a subtraction that isn't a clean 'ex'). Grounded on the descriptions."""
    lines = "\n".join(f"  - {o.get('code', '?')}: {o.get('descriptor', '')}"
                      for o in operands)
    obj = _opus_json_object(
        _COMPOSITE_PROMPT.format(operation=operation, operands=lines))
    label = (obj.get("label") or "").strip()
    if not label:
        raise ValueError("Opus returned no usable composite legend label")
    return {"label": label, "note": (obj.get("note") or "").strip()}


def propose_titles(subject: str, series: list[str], transform: str,
                   span: str, n: int = 2) -> list[dict]:
    """Call Opus and return [{title, subtitle}, ...]; raises if the key is unset
    or the output can't be parsed (fail loud — never ship a fabricated title)."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY not set — cannot propose titles.")
    import anthropic

    prompt = _PROMPT.format(n=n, subject=subject, series="; ".join(series),
                            transform=transform, span=span)
    msg = anthropic.Anthropic().messages.create(
        model=MODEL, max_tokens=700,
        messages=[{"role": "user", "content": prompt}])
    text = "".join(getattr(b, "text", "") for b in msg.content)
    opts = _extract_json_array(text)
    out = [{"title": o["title"].strip(), "subtitle": o.get("subtitle", "").strip()}
           for o in opts if o.get("title")]
    if not out:
        raise ValueError("Opus returned no usable title options")
    return out
