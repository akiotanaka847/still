# Still Studio — Vision + Image-Normalization Pipeline

> Status: **spec, build-ready (F1).** Author: AI Engineer. Companion to `docs/ROADMAP_PLATAFORMA.md`.
> Scope: the pipeline that feeds the deterministic engine (`layout.py` + `renderer.py`). **This pipeline never composes.**

## 0. The sacred frontier (restated)

The AI decides ONLY `{order, hero, sizes, reasoning}`:
- `order` — visual order of products
- `hero` — the prominent product
- `sizes` — each product's **real relative physical size** (1L bottle ≫ 30ml serum), NOT photo crop size
- `reasoning` — short explanation

Everything geometric (placement, scaling-to-area, rows, overlap, shadow, background compositing) stays in `layout.py` / `renderer.py`. This pipeline produces **clean packshots + features**, and supplies the vision contract. Nothing here moves a pixel of composition.

---

## 1. End-to-end normalization pipeline

### 1.1 Stages

```
INGEST            raw upload (mixed states) → SKU-named file on disk
  │
  ▼
ALPHA CHECK       has a real alpha channel with transparency?  →  yes / no
  │
  ├── transparent ──────────────────────────────────► (skip bg removal)
  │
  └── opaque ──► BG REMOVAL (rembg CPU F1 → BiRefNet GPU F2) ──►
  │
  ▼
TRIM              crop to content bbox via alpha (tight bbox)
  │
  ▼
FEATURES          dominant color, orientation, aspect ratio, real-size hint
  │
  ▼
PACKSHOT          clean RGBA PNG (transparent, tight bbox) + features.json
                  → this, not the raw upload, is what the engine consumes
```

### 1.2 Why this changes the current flow

Today `renderer.py::_load_trimmed` trims **at render time, every render**, and for opaque images it guesses the background from the 4 corners (`_content_bbox`, color-tolerance heuristic). That is risk #1: fragile, repeated, and silently degrading.

The fix: **normalize ONCE at ingest** into a guaranteed-transparent, tight-bbox packshot. After normalization, every opaque image already has a real alpha channel, so the corner-color branch in `_content_bbox` becomes dead code on the happy path (kept only as a last-resort fallback). `renderer.py` then only does `alpha.getbbox()` on already-clean inputs — fast and deterministic.

### 1.3 New module: `normalize.py`

A standalone module (no FastAPI, no engine imports) so it runs identically in the request path (F1) and a worker loop (F2).

```python
# normalize.py
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path
from typing import Optional
from PIL import Image

ALPHA_PRESENCE_THRESHOLD = 250   # min alpha considered "fully opaque"
ALPHA_COVERAGE_FRAC      = 0.995 # if ≥99.5% of pixels are opaque → treat as opaque image

class BgMethod(str, Enum):
    NATIVE_ALPHA = "native_alpha"   # already transparent → trim only
    REMBG        = "rembg"          # F1 CPU
    BIREFNET     = "birefnet"       # F2 GPU
    CORNER_COLOR = "corner_color"   # last-resort fallback (legacy heuristic)

class NormalizeStatus(str, Enum):
    OK       = "ok"
    DEGRADED = "degraded"   # produced output but low confidence (flag for review)
    FAILED   = "failed"     # no usable packshot (do NOT silently pass raw through)

@dataclass
class Features:
    width: int                 # tight-bbox content width  (feeds layout.py)
    height: int                # tight-bbox content height
    aspect_ratio: float        # width / height
    orientation: str           # "portrait" | "landscape" | "square"
    dominant_color: tuple      # (r, g, b) of the product content only
    coverage: float            # fraction of bbox area that is non-transparent
    real_size_hint: Optional[float] = None  # 0..1 prior for the vision step (see §2.3)

@dataclass
class NormalizeResult:
    status: NormalizeStatus
    method: BgMethod
    packshot_path: Optional[str]      # clean RGBA PNG (transparent, tight bbox)
    features: Optional[Features]
    confidence: float                 # 0..1
    error: Optional[str] = None


def has_real_alpha(img: Image.Image) -> bool:
    """True only if the image has an alpha channel AND meaningful transparency."""
    if img.mode not in ("RGBA", "LA", "P"):
        return False
    rgba = img.convert("RGBA")
    alpha = rgba.split()[-1]
    lo, hi = alpha.getextrema()
    if hi == 0:                       # fully transparent file → treat as broken/opaque path
        return False
    # count opaque pixels; if almost all opaque, the "alpha" is decorative, not a cutout
    opaque = alpha.point(lambda a: 255 if a >= ALPHA_PRESENCE_THRESHOLD else 0)
    opaque_frac = sum(opaque.getdata()) / 255 / (img.width * img.height)
    return opaque_frac < ALPHA_COVERAGE_FRAC


def remove_background(img: Image.Image, method: BgMethod) -> tuple[Image.Image, float]:
    """Returns (rgba_with_alpha, confidence). Raises on hard failure."""
    if method == BgMethod.REMBG:
        from rembg import remove                      # F1: CPU, isnet/u2net session
        out = remove(img, post_process_mask=True)     # returns RGBA
        return out.convert("RGBA"), _mask_confidence(out)
    if method == BgMethod.BIREFNET:
        return _birefnet_remove(img)                  # F2: GPU worker, §1.6
    raise ValueError(f"unsupported bg method {method}")


def trim_to_content(img: Image.Image, alpha_threshold: int = 10) -> Image.Image:
    """Tight crop using the alpha channel. Engine-compatible with renderer._content_bbox."""
    rgba = img.convert("RGBA")
    mask = rgba.split()[-1].point(lambda a: 255 if a > alpha_threshold else 0)
    bbox = mask.getbbox()
    return rgba.crop(bbox) if bbox else rgba


def extract_features(packshot: Image.Image) -> Features:
    rgba = packshot.convert("RGBA")
    w, h = rgba.size
    ar = w / max(h, 1)
    orientation = "square" if 0.95 <= ar <= 1.05 else ("landscape" if ar > 1 else "portrait")
    dom = _dominant_color(rgba)        # k-means/quantize over non-transparent pixels only
    cov = _alpha_coverage(rgba)
    return Features(w, h, round(ar, 4), orientation, dom, round(cov, 4))


def normalize_image(
    src_path: str,
    out_path: str,
    bg_method: BgMethod = BgMethod.REMBG,
) -> NormalizeResult:
    """ONE image: ingest → alpha check → (bg removal) → trim → features → packshot."""
    try:
        img = Image.open(src_path)
        img.load()
    except Exception as e:
        return NormalizeResult(NormalizeStatus.FAILED, bg_method, None, None, 0.0, f"open: {e}")

    try:
        if has_real_alpha(img):
            method, conf = BgMethod.NATIVE_ALPHA, 1.0
            work = img.convert("RGBA")
        else:
            try:
                work, conf = remove_background(img, bg_method)
                method = bg_method
            except Exception as e:
                # explicit failure path — do NOT silently degrade to raw opaque image
                return NormalizeResult(NormalizeStatus.FAILED, bg_method, None, None, 0.0, f"bg: {e}")

        packshot = trim_to_content(work)
        if packshot.width < 8 or packshot.height < 8:
            return NormalizeResult(NormalizeStatus.FAILED, method, None, None, conf, "empty bbox")

        feats = extract_features(packshot)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        packshot.save(out_path, "PNG", optimize=True)

        status = NormalizeStatus.OK if conf >= 0.6 else NormalizeStatus.DEGRADED
        return NormalizeResult(status, method, out_path, feats, conf)
    except Exception as e:
        return NormalizeResult(NormalizeStatus.FAILED, bg_method, None, None, 0.0, f"normalize: {e}")
```

Helpers `_mask_confidence`, `_dominant_color`, `_alpha_coverage`, `_birefnet_remove` are in §1.6 / §2.

### 1.4 Failure handling (never silently degrade — roadmap requirement)

| Case | Action |
|---|---|
| File won't open | `FAILED` — surface to user, exclude SKU from group |
| rembg/BiRefNet throws | `FAILED` — do **not** fall back to raw opaque image (would composite a white box) |
| Empty/tiny bbox after trim | `FAILED` — cutout removed the product |
| Low mask confidence (< 0.6) | `DEGRADED` — produce packshot but flag for manual review |
| `has_real_alpha` ambiguous | prefer bg removal; cheaper than shipping a haloed cutout |

`DEGRADED` and `FAILED` SKUs are reported per-group (counts + thumbnails) so a human reviews before the group is composited. This is the gate protecting risk #1.

### 1.5 Where it slots into the current flow

**`main.py::bodegon_upload`** — after writing each file to disk, before `get_image_dimensions`:

```python
# main.py (inside the upload loop, replacing the direct get_image_dimensions call)
from normalize import normalize_image, NormalizeStatus, BgMethod

raw_dest = products_dir / safe
raw_dest.write_bytes(data)

pack_dest = products_dir / f"{Path(safe).stem}.packshot.png"
res = normalize_image(str(raw_dest), str(pack_dest),
                      bg_method=BgMethod(os.environ.get("BG_METHOD", "rembg")))

if res.status == NormalizeStatus.FAILED:
    failures.append({"filename": safe, "error": res.error})
    raw_dest.unlink(missing_ok=True)
    continue

images.append({
    "name": Path(safe).stem.upper().replace(" ", "_"),
    "filename": safe,
    "filepath": str(pack_dest),          # ← engine consumes the PACKSHOT, not the raw upload
    "raw_filepath": str(raw_dest),
    "width": res.features.width,         # tight-bbox dims (no second trim needed)
    "height": res.features.height,
    "features": asdict(res.features),    # dominant_color, orientation, ratio, real_size_hint
    "normalize": {"status": res.status, "method": res.method, "confidence": res.confidence},
})
```

Return payload gains `failures` and `degraded` arrays so the UI can show the review gate.

**Consequences downstream (no engine rewrite needed):**
- `layout.py` already consumes `width/height` + `scale`; it now gets tight-bbox dims directly. No change.
- `renderer.py::_load_trimmed` now opens an already-tight, already-transparent packshot → `alpha.getbbox()` is a near-noop and the corner-color branch never triggers. No change required, but add a one-line comment that inputs are pre-normalized. The legacy corner-color code stays only as defense-in-depth.
- The single-upload "render" path and the batch path both benefit because they read `filepath` (now the packshot).

### 1.6 Model path F1 → F2 (throughput / quality tradeoffs)

| | F1 — rembg (CPU) | F2 — BiRefNet (GPU worker) |
|---|---|---|
| Model | `isnet-general-use` / `u2net` via `rembg` | BiRefNet (SOTA dichotomous segmentation) |
| Hardware | any CPU (laptop, cheap container) | one GPU worker (T4/A10/L4 class) |
| Throughput | ~0.5–2 s/img CPU (serial) | ~30–80 ms/img batched on GPU |
| Quality | good on clean studio shots; struggles on fine edges (hair, transparent glass, reflections) | crisp edges, handles glass/reflective packaging, thin straps, foam |
| When | MVP batch (hundreds–1000), validate the pipeline | platform scale, premium SKUs |
| Cost | $0/img, but CPU-time bound | $0/img marginal, GPU instance amortized |

**Migration is a one-line swap** because both go through `remove_background(img, method)` returning `(rgba, confidence)`. F2 adds:
- `_birefnet_remove(img)` running on the GPU worker (load model once, keep resident).
- **Batched inference**: the worker pulls N pending packshots and runs them as one GPU batch (the throughput win). The job-queue (`jobs` table, roadmap F2) feeds it.
- Same `Features` extraction and trim run after, unchanged.

Keep rembg available in F2 as a CPU fallback when the GPU worker is saturated or down (degrades throughput, not quality of the pipeline contract).

---

## 2. Feature extraction (per packshot)

Extracted in `extract_features` on the **content-only** (non-transparent) pixels.

| Feature | How | Used by |
|---|---|---|
| `width`, `height` | tight bbox after trim | `layout.py` aspect/scaling (already its inputs) |
| `aspect_ratio` | `w/h` | orientation + `alternado` rhythm strategy |
| `orientation` | `square` (0.95–1.05), else portrait/landscape | strategy selection, balance |
| `dominant_color` | quantize to a small palette over non-transparent pixels, pick the modal bucket; ignore near-white/near-transparent | color-balance ordering hint + style/background harmony |
| `coverage` | non-transparent area / bbox area | cutout sanity (low coverage on a "bottle" → suspect mask) |
| `real_size_hint` | optional prior (§2.3) | seeds/sanity-checks the vision `sizes` |

```python
def _dominant_color(rgba):
    px = [p[:3] for p in rgba.getdata() if p[3] > 200]      # opaque pixels only
    if not px: return (200, 200, 200)
    q = Image.new("RGB", (len(px), 1)); q.putdata(px)
    q = q.quantize(colors=8)                                # 8-bucket palette
    pal = q.getpalette(); counts = q.getcolors()
    _, idx = max(counts)                                    # modal bucket
    return tuple(pal[idx*3: idx*3+3])

def _alpha_coverage(rgba):
    a = rgba.split()[-1]
    return sum(a.point(lambda v: 1 if v > 10 else 0).getdata()) / (rgba.width * rgba.height)
```

### 2.3 Real-size hint (optional prior, not a decision)

The **vision step owns `sizes`** (the sacred frontier). Features may only supply a *hint* the prompt can use or override. Two cheap signals:
- **SKU-name keyword map** (e.g. regex for `30ML`, `1L`, `250G`) → coarse size bucket when present in the filename/SKU.
- **Bbox pixel height** is explicitly NOT a size signal (crop size ≠ physical size) and must not be used as one.

If neither is reliable, `real_size_hint = None` and the vision model decides unaided. The hint is passed into the prompt as advisory context, never written directly to `sizes`.

---

## 3. Curated background-style system

Backgrounds are **curated template assets composited by the deterministic renderer** (no generative AI in F1, per roadmap). Five named styles + optional podium/scene layers.

### 3.1 Asset layout

```
assets/styles/
  minimalista/   style.json   bg.png   [podium.png]
  premium/       style.json   bg.png    podium.png
  natural/       style.json   bg.png   [scene.png]
  colorido/      style.json   bg.png
  editorial/     style.json   bg.png    scene.png
```

### 3.2 Style schema (`style.json`)

```jsonc
{
  "id": "premium",
  "label": "Premium",
  "version": 1,
  "background": {
    "type": "image",            // "image" | "gradient" | "solid"
    "asset": "bg.png",          // tiled/scaled to canvas by the renderer
    "fit": "cover",             // "cover" | "contain" | "tile"
    "solid": null,              // used when type=="solid": [r,g,b]
    "gradient": null            // used when type=="gradient": {from,to,angle}
  },
  "podium": {                   // optional surface the products sit on
    "asset": "podium.png",
    "anchor": "bottom-center",
    "scale": 0.85,             // fraction of canvas width
    "y_offset_frac": 0.0
  },
  "scene": null,               // optional foreground/overlay asset (e.g. editorial props)
  "lighting": {
    "shadow_opacity": 0.32,    // overrides layout.py effect defaults for this style
    "shadow_blur_frac": 0.045,
    "contact_shadow": true
  },
  "palette": {
    "base": [18, 18, 20],      // for color-harmony checks vs dominant_color
    "accent": [201, 162, 39]
  },
  "padding_frac": 0.08,        // breathing room added around the product cluster
  "safe_area_frac": 0.06       // keep products out of the outer margin
}
```

### 3.3 How the renderer applies it (engine stays deterministic)

`render_png` already accepts `background_path` and `effects`. Extend it to accept a resolved **style dict** (loaded from `style.json`) instead of just a path — purely additive, the AI never touches it:

```python
# renderer.py — additive, no compositional decision moves to AI
def render_png(layout, output_path, style: dict | None = None, background_path=None):
    canvas = Image.new("RGBA", (cw, ch), (0,0,0,0))
    if style:
        _paint_background(canvas, style["background"])      # solid/gradient/image per schema
        if style.get("podium"):
            _paint_podium(canvas, style["podium"])          # before products
    elif background_path:
        ... # legacy single-image path (kept)
    # shadows + products exactly as today (z-order, contact shadow)
    # style["lighting"] overrides effects.shadow_opacity / blur if present
    if style and style.get("scene"):
        _paint_scene(canvas, style["scene"])                # overlay after products
```

Selection is a user/UI choice (or per-group default), passed as `style_id` in the render request; `main.py` loads `assets/styles/<id>/style.json`. The renderer reads geometry from `layout.py` and pixels from the style asset. **The product positions/scales are 100% from the engine; the style only paints background, podium, scene, and tweaks shadow params.**

---

## 4. Vision step contract & quality

### 4.1 JSON schema (output)

```jsonc
{
  "order":     ["SKU_A", "SKU_B", "SKU_C"],   // permutation of input names, exact strings
  "hero":      "SKU_B",                         // must be one of order
  "sizes":     { "SKU_A": 1.0, "SKU_B": 0.55, "SKU_C": 0.3 },  // real physical size, 0.3..1.0
  "reasoning": "1–2 sentences (es) covering order + sizes"
}
```

### 4.2 Prompt (single source of truth)

Keep the existing `_build_prompt` in `ai_proposal.py` as the canonical prompt (it already encodes the sacred-frontier rules: real physical size, not crop size; 0.3–1.0; hero; order criteria). **Do not fork it** between modes — both agent-native and API modes send the same prompt + the same images so the contract is identical.

Augment it only with optional advisory context when available (never as commands):
- per-product `dominant_color` + `orientation` (helps color-balance ordering),
- `real_size_hint` bucket from §2.3 when the SKU name carries a volume/weight.

### 4.3 Validation / normalization (already partially present)

Reuse and harden `ai_proposal.py::_parse_result` + `_normalize_sizes`. Validation rules (enforced server-side, after either mode):

1. **Strip markdown fences**, extract first `{...}` block (existing).
2. **`order`**: keep only valid names, append any missing in input order (existing). Reject if result empty.
3. **`hero`**: must be in `order`; else default to `order[0]`.
4. **`sizes`**: `_normalize_sizes` — max → 1.0, rescale others proportionally, clamp `[0.3, 1.0]`, fill missing with neutral `0.7×max`. Case-insensitive key match (existing).
5. **`reasoning`**: default "".
6. **Sanity vs features (new, soft)**: if `sizes` disagrees wildly with a confident `real_size_hint` (e.g. names a 30ml serum as 1.0 over a 1L bottle), log a warning; do not override (model may have context the hint lacks). Surfacing only.

These rules are the boundary that keeps a hallucinated/garbled response from breaking the deterministic engine.

### 4.4 Agent-native mode vs external-API mode (licensing boundary)

| | Agent-native (qaio) | External API key |
|---|---|---|
| Who runs vision | the qaio agent's own multimodal capability (qaio subscription) | Groq / Gemini / Claude SDK in `ai_proposal.py` |
| Use case | internal / low-volume | SaaS / mass-batch |
| Image limit | high (agent multimodal) | **Groq ≤ ~5 images** per vision request |
| Contract | identical JSON schema (§4.1), identical prompt (§4.2), identical validation (§4.3) | same |

Selector: `AI_PROVIDER=qaio` routes to the agent path; otherwise the existing `groq → gemini → claude` auto-resolution in `analyze_products`. Add a `qaio` branch to the dispatcher that hands the same `content` (text labels + packshot JPEGs + prompt) to the agent's multimodal call and feeds the raw text back through `_parse_result(raw, names)` — so **one validation path serves all providers**.

Feed the **packshots** (transparent, tight) to vision, not raw uploads — cleaner product reasoning and smaller/faster JPEG thumbnails.

### 4.5 The >5-image Groq degradation

`_analyze_groq` already caps attached images at `GROQ_MAX_IMAGES = 5` and passes the rest as text-only names. That means **with >5 products on Groq, products 6+ are ordered/sized from their NAME alone** — degraded quality (no visual signal). Required handling:

- **Surface it**: when `len(images) > 5` and provider is Groq, return a `degraded_vision: true` flag + message ("Groq analiza solo 5 imágenes; el resto se ordenó por nombre").
- **Prefer agent-native or Gemini/Claude** for groups > 5 (no per-image limit on the agent path).
- **F2**: for mass-batch on Groq, **tile** up to N product thumbnails into a single labeled contact-sheet image (one image, many products) to stay under the limit while keeping visual signal — a clean upgrade path, contract unchanged.

---

## 5. Test / validation strategy (risk #1: normalization at scale)

The whole platform's visual quality rides on packshot consistency. Validation is layered:

### 5.1 Golden corpus
Curate ~50–100 representative SKUs spanning the hard cases:
- already-transparent PNGs, opaque JPEGs on white, opaque on textured/colored backgrounds,
- glass/reflective bottles, dark products on dark bg, near-white products on white,
- portrait/landscape/square, multi-object frames (should be rejected/flagged).

Store expected packshots + expected `Features` (within tolerance).

### 5.2 Automated checks per packshot
- **Transparency invariant**: output is RGBA and has real transparency (`has_real_alpha == True`).
- **Tight-bbox invariant**: `trim_to_content(packshot)` returns same size (no slack margin) ± 2 px.
- **Coverage band**: `coverage` within sane bounds per category (a bottle with 4% coverage = bad mask → assert FAILED/DEGRADED, not OK).
- **No-halo check**: count semi-transparent edge pixels (alpha 10–245) as a fraction of perimeter; spike = haloing → DEGRADED.
- **Stability**: same input → identical output bytes (determinism for caching/regression).
- **Feature accuracy**: `dominant_color` within ΔE threshold of hand-labeled truth; `orientation` exact match.

### 5.3 Confidence calibration
Plot `confidence` vs human pass/fail on the corpus to set the `0.6` OK/DEGRADED threshold empirically. Track precision/recall of the FAILED/DEGRADED gate — the metric that matters is **"bad packshots that reached compositing"** (should trend to ~0).

### 5.4 Visual regression (engine end-to-end)
Run normalize → `compute_layout` → `render_png` on the corpus for each style; diff rendered PNGs against approved baselines (perceptual hash / SSIM). Flag drift when normalize or styles change.

### 5.5 Scale / throughput
- Batch-run the full corpus ×N to measure rembg CPU throughput and set worker concurrency for F1.
- Track p50/p95 per-image latency and FAILED-rate as a CI gate (regress if FAILED-rate rises).
- F2: same suite against BiRefNet GPU, asserting quality ≥ F1 and throughput target met.

### 5.6 Vision-contract tests (no LLM needed)
Unit-test `_parse_result` / `_normalize_sizes` against adversarial inputs: markdown-fenced JSON, missing keys, extra/wrong names, out-of-range sizes, all-equal sizes, empty `order`, case-mismatched keys, Groq >5 degradation flag. These guarantee the engine always receives a valid `{order, hero, sizes}` regardless of model behavior.

---

## Appendix — file/function index (F1 build list)

| File | Change |
|---|---|
| `normalize.py` (new) | `has_real_alpha`, `remove_background`, `trim_to_content`, `extract_features`, `normalize_image`, `Features`, `NormalizeResult`, enums |
| `main.py` | upload loop calls `normalize_image`; payload gains `failures`/`degraded`; render accepts `style_id` |
| `renderer.py` | `render_png(..., style=...)`; add `_paint_background/_podium/_scene`; note inputs are pre-normalized |
| `ai_proposal.py` | add `qaio` agent-native branch in dispatcher; surface `degraded_vision` for Groq >5; reuse `_parse_result` for all modes |
| `assets/styles/<id>/` (new) | `style.json` + `bg.png` (+ podium/scene) for the 5 styles |
| `requirements.txt` | add `rembg` (F1); `onnxruntime` is pulled in by rembg |
| `tests/` (new) | golden corpus + checks §5.2–§5.6 |
