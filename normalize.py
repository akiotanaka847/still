"""
Ingest-time image normalization for Still Studio (F1).

Implements the normalization pipeline from ``docs/platform/VISION_PIPELINE.md``:

    load -> alpha check -> (bg removal if opaque) -> trim to content bbox -> features

The output is a guaranteed-transparent, tight-bbox RGBA packshot plus a
``Features`` record. This module is standalone (no FastAPI, no engine imports)
so it runs identically in the request path (F1) and a worker loop (F2).

Sacred-frontier note: feature extraction NEVER uses bbox pixel height as a real
physical-size signal. The vision step alone owns ``sizes``. ``real_size_hint`` is
an optional advisory prior derived from SKU-name keywords only.

Failure policy (roadmap risk #1, "no silent failures"): on a bad cutout, an
unopenable file, or a rembg error this returns an explicit FAILED/DEGRADED
status and NEVER passes a raw white-box image downstream.

Python 3.9 compatible (typing uses Optional/Tuple, not PEP 604 unions).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image

# ─── Tunables (from spec §1.3) ────────────────────────────────────────────────
ALPHA_PRESENCE_THRESHOLD = 250    # min alpha value considered "fully opaque"
ALPHA_COVERAGE_FRAC = 0.995       # >= this fraction opaque -> treat as opaque image
TRIM_ALPHA_THRESHOLD = 10         # alpha > this counts as content when trimming
MIN_PACKSHOT_SIDE = 8             # smaller than this after trim -> cutout removed product
OK_CONFIDENCE = 0.6               # >= OK, below -> DEGRADED (spec §1.4)

# SKU-name volume/weight keywords -> coarse real-size buckets (spec §2.3).
# These are advisory priors only; the vision model may override.
#   - (?<!\d) so we match the start of the number (handles "SERUM_30ML": the
#     underscore is a word char, so \b would not fire before the digit).
#   - (?![A-Za-z]) so the unit isn't part of a longer word.
#   - Order matters: multi-letter units (ML, CL, KG) are tested before the
#     single-letter L/G to avoid "30ML" matching the bare "L" rule.
_SIZE_HINT_PATTERNS = [
    (re.compile(r"(?<!\d)\d+\s*ML(?![A-Za-z])", re.I), 0.35),   # e.g. 30ML serum
    (re.compile(r"(?<!\d)\d+\s*CL(?![A-Za-z])", re.I), 0.55),
    (re.compile(r"(?<!\d)\d+\s*KG(?![A-Za-z])", re.I), 1.0),
    (re.compile(r"(?<!\d)\d+\s*G(?![A-Za-z])", re.I), 0.55),    # grams
    (re.compile(r"(?<!\d)\d+\s*L(?![A-Za-z])", re.I), 1.0),     # litre bottle
]


class BgMethod(str, Enum):
    """Background-removal seam. New backends swap in here without touching callers."""
    NATIVE_ALPHA = "native_alpha"   # already transparent -> trim only
    REMBG = "rembg"                 # F1 CPU
    BIREFNET = "birefnet"           # F2 GPU (not implemented in F1)
    CORNER_COLOR = "corner_color"   # last-resort legacy heuristic (renderer fallback)


class NormalizeStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"   # produced output but low confidence (flag for review)
    FAILED = "failed"       # no usable packshot (do NOT silently pass raw through)


@dataclass
class Features:
    """Per-packshot features extracted from content-only (non-transparent) pixels."""
    width: int                              # tight-bbox content width (feeds layout.py)
    height: int                             # tight-bbox content height
    aspect_ratio: float                     # width / height
    orientation: str                        # "portrait" | "landscape" | "square"
    dominant_color: Tuple[int, int, int]    # (r, g, b) of product content only
    coverage: float                         # non-transparent area / bbox area
    real_size_hint: Optional[float] = None  # advisory 0..1 prior (SKU-name keyword)


@dataclass
class NormalizeResult:
    status: NormalizeStatus
    method: BgMethod
    packshot_path: Optional[str]            # clean RGBA PNG (transparent, tight bbox)
    features: Optional[Features]
    confidence: float                       # 0..1
    error: Optional[str] = None


# ─── Alpha detection ──────────────────────────────────────────────────────────
def has_real_alpha(img: Image.Image) -> bool:
    """
    True only if the image has an alpha channel AND meaningful transparency.

    A fully-transparent file (broken export) or a decorative alpha where nearly
    every pixel is opaque is treated as "no real alpha" so it goes through bg
    removal instead of shipping a haloed/raw cutout.
    """
    if img.mode not in ("RGBA", "LA", "P", "PA"):
        return False
    rgba = img.convert("RGBA")
    alpha = rgba.split()[-1]
    lo, hi = alpha.getextrema()
    if hi == 0:                       # fully transparent -> broken, use opaque path
        return False
    # Fraction of (near-)fully-opaque pixels. If almost all opaque, the alpha is
    # decorative rather than a real cutout.
    opaque = alpha.point(lambda a: 255 if a >= ALPHA_PRESENCE_THRESHOLD else 0)
    total = img.width * img.height
    opaque_frac = (sum(opaque.getdata()) / 255) / total if total else 1.0
    return opaque_frac < ALPHA_COVERAGE_FRAC


# ─── Background removal (single seam: rembg F1 -> BiRefNet F2) ─────────────────
def remove_background(img: Image.Image, method: BgMethod) -> Tuple[Image.Image, float]:
    """
    Remove the background, returning ``(rgba_with_alpha, confidence)``.

    Raises on hard failure (missing dependency, model download offline, runtime
    error) so the caller can record an explicit FAILED status. This is the single
    seam through which BiRefNet/GPU swap in later (spec §1.6).
    """
    if method == BgMethod.REMBG:
        try:
            from rembg import remove  # lazy import: heavy, optional dependency
        except Exception as e:  # noqa: BLE001 - surface as hard failure to caller
            raise RuntimeError(f"rembg unavailable: {e}") from e
        # On first ever run rembg downloads ~176MB (u2net.onnx) to ~/.u2net.
        # Any network/runtime error propagates and is recorded as FAILED upstream.
        out = remove(img, post_process_mask=True)  # returns RGBA
        rgba = out.convert("RGBA")
        return rgba, _mask_confidence(rgba)
    if method == BgMethod.BIREFNET:
        raise NotImplementedError("BiRefNet (F2) not available in F1")
    raise ValueError(f"unsupported bg method {method}")


def _mask_confidence(rgba: Image.Image) -> float:
    """
    Heuristic confidence for a produced cutout, 0..1.

    Penalizes two failure modes: (1) the mask kept almost nothing or almost
    everything (coverage extremes), and (2) heavy haloing (many semi-transparent
    edge pixels). Calibrated to be conservative; the gate that matters is keeping
    bad packshots from reaching compositing, not a precise score.
    """
    alpha = rgba.split()[-1]
    total = rgba.width * rgba.height
    if total == 0:
        return 0.0
    hist = alpha.histogram()
    content = sum(hist[TRIM_ALPHA_THRESHOLD + 1:])
    coverage = content / total
    if coverage <= 0.0:
        return 0.0
    # Semi-transparent (halo) pixels: alpha in (10, 245).
    halo = sum(hist[TRIM_ALPHA_THRESHOLD + 1:246])
    halo_frac = halo / max(content, 1)

    conf = 1.0
    if coverage < 0.02:            # removed almost everything -> suspect
        conf *= 0.3
    elif coverage > 0.97:          # removed almost nothing -> suspect
        conf *= 0.5
    conf *= max(0.0, 1.0 - halo_frac)   # haloing drags confidence down
    return round(max(0.0, min(1.0, conf)), 4)


# ─── Trim to tight content bbox ───────────────────────────────────────────────
def trim_to_content(img: Image.Image, alpha_threshold: int = TRIM_ALPHA_THRESHOLD) -> Image.Image:
    """
    Tight crop using the alpha channel. Engine-compatible with
    ``renderer._content_bbox`` (alpha-first path). Returns an RGBA image.
    """
    rgba = img.convert("RGBA")
    mask = rgba.split()[-1].point(lambda a: 255 if a > alpha_threshold else 0)
    bbox = mask.getbbox()
    return rgba.crop(bbox) if bbox else rgba


# ─── Feature extraction (content-only pixels) ─────────────────────────────────
def _dominant_color(rgba: Image.Image) -> Tuple[int, int, int]:
    """Modal color over opaque (alpha > 200) pixels via an 8-bucket quantize."""
    px = [p[:3] for p in rgba.getdata() if p[3] > 200]
    if not px:
        return (200, 200, 200)
    q = Image.new("RGB", (len(px), 1))
    q.putdata(px)
    q = q.quantize(colors=8)
    pal = q.getpalette() or []
    counts = q.getcolors() or []
    if not counts or not pal:
        return (200, 200, 200)
    _, idx = max(counts)                       # modal bucket
    return tuple(pal[idx * 3: idx * 3 + 3])    # type: ignore[return-value]


def _alpha_coverage(rgba: Image.Image) -> float:
    """Fraction of bbox area that is non-transparent."""
    total = rgba.width * rgba.height
    if total == 0:
        return 0.0
    a = rgba.split()[-1]
    content = sum(a.point(lambda v: 1 if v > TRIM_ALPHA_THRESHOLD else 0).getdata())
    return content / total


def _size_hint_from_name(name: Optional[str]) -> Optional[float]:
    """
    Advisory real-size prior from a SKU/filename volume-weight keyword (spec §2.3).

    Bbox pixel height is explicitly NOT used here (crop size != physical size).
    Returns None when no reliable keyword is present.
    """
    if not name:
        return None
    for pat, hint in _SIZE_HINT_PATTERNS:
        if pat.search(name):
            return hint
    return None


def extract_features(packshot: Image.Image, name: Optional[str] = None) -> Features:
    """Compute features over the trimmed packshot's content-only pixels."""
    rgba = packshot.convert("RGBA")
    w, h = rgba.size
    ar = w / max(h, 1)
    if 0.95 <= ar <= 1.05:
        orientation = "square"
    elif ar > 1:
        orientation = "landscape"
    else:
        orientation = "portrait"
    return Features(
        width=w,
        height=h,
        aspect_ratio=round(ar, 4),
        orientation=orientation,
        dominant_color=_dominant_color(rgba),
        coverage=round(_alpha_coverage(rgba), 4),
        real_size_hint=_size_hint_from_name(name),
    )


# ─── Pipeline entry point ─────────────────────────────────────────────────────
def normalize_image(
    src_path: str,
    out_path: str,
    bg_method: BgMethod = BgMethod.REMBG,
    name: Optional[str] = None,
) -> NormalizeResult:
    """
    Normalize ONE image: ingest -> alpha check -> (bg removal) -> trim -> features.

    Never raises for expected failure modes; returns an explicit FAILED/DEGRADED
    ``NormalizeResult`` instead. On success the tight transparent packshot is
    written to ``out_path`` and the result carries its ``Features``.
    """
    try:
        img = Image.open(src_path)
        img.load()
    except Exception as e:  # noqa: BLE001
        return NormalizeResult(NormalizeStatus.FAILED, bg_method, None, None, 0.0, f"open: {e}")

    try:
        if has_real_alpha(img):
            method, conf = BgMethod.NATIVE_ALPHA, 1.0
            work = img.convert("RGBA")
        else:
            try:
                work, conf = remove_background(img, bg_method)
                method = bg_method
            except Exception as e:  # noqa: BLE001
                # Explicit failure: do NOT silently degrade to the raw opaque
                # image (that would composite a white box downstream).
                return NormalizeResult(
                    NormalizeStatus.FAILED, bg_method, None, None, 0.0, f"bg: {e}"
                )

        packshot = trim_to_content(work)
        if packshot.width < MIN_PACKSHOT_SIDE or packshot.height < MIN_PACKSHOT_SIDE:
            return NormalizeResult(
                NormalizeStatus.FAILED, method, None, None, conf, "empty bbox"
            )

        feats = extract_features(packshot, name=name)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        packshot.save(out_path, "PNG", optimize=True)

        status = NormalizeStatus.OK if conf >= OK_CONFIDENCE else NormalizeStatus.DEGRADED
        return NormalizeResult(status, method, out_path, feats, conf)
    except Exception as e:  # noqa: BLE001
        return NormalizeResult(NormalizeStatus.FAILED, bg_method, None, None, 0.0, f"normalize: {e}")
