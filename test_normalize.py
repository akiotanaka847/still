"""
Tests for the F1 ingest-time normalization pipeline (normalize.py).

Run:
    .venv-test/bin/python -m pytest test_normalize.py -v
    .venv-test/bin/python test_normalize.py            # plain runner, no pytest

Tests are split into two tiers:
  * MODEL-FREE  — alpha detection, trimming, features, failure handling. Always run.
  * MODEL       — opaque image -> rembg bg removal. Skipped automatically when the
                  rembg model is unavailable/offline (so CI never hangs).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

import normalize as N
from normalize import (
    BgMethod,
    NormalizeStatus,
    extract_features,
    has_real_alpha,
    normalize_image,
    trim_to_content,
)


# ─── Synthetic fixtures ───────────────────────────────────────────────────────
def _make_transparent_png(path: str, w: int = 200, h: int = 300) -> None:
    """Transparent RGBA with a centered opaque blob and empty margins."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    m = int(min(w, h) * 0.2)
    d.rounded_rectangle([m, m, w - m, h - m], radius=20, fill=(40, 120, 200, 255))
    img.save(path)


def _make_opaque_jpeg(path: str, w: int = 200, h: int = 200) -> None:
    """Opaque JPEG: red product on a white background (the classic studio shot)."""
    img = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.ellipse([60, 40, 140, 160], fill=(200, 40, 40))
    img.save(path, "JPEG", quality=95)


def _rembg_available() -> bool:
    """True only if rembg imports AND its model is already cached (no download)."""
    try:
        import rembg  # noqa: F401
    except Exception:
        return False
    return Path(os.path.expanduser("~/.u2net/u2net.onnx")).exists()


# ─── MODEL-FREE tests ─────────────────────────────────────────────────────────
def test_already_transparent_is_trimmed_only():
    """An already-transparent PNG should be trimmed (NATIVE_ALPHA), not bg-removed."""
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "serum_30ml.png")
        out = os.path.join(tmp, "serum.packshot.png")
        _make_transparent_png(src, 200, 300)

        res = normalize_image(src, out, name="SERUM_30ML")

        assert res.status == NormalizeStatus.OK, res.error
        assert res.method == BgMethod.NATIVE_ALPHA
        assert res.packshot_path and Path(out).exists()
        # Tight bbox: margins (20%) removed -> ~120x180 content from a 200x300 frame.
        assert res.features.width < 200 and res.features.height < 300
        # Output is RGBA with real transparency.
        packshot = Image.open(out)
        assert packshot.mode == "RGBA"
        assert has_real_alpha(packshot)
        # SKU-name "30ML" yields a small real-size hint (advisory only).
        assert res.features.real_size_hint == 0.35
        print(f"  transparent -> {res.features.width}x{res.features.height} "
              f"method={res.method.value} hint={res.features.real_size_hint}")


def test_corrupt_image_returns_failed_not_crash():
    """A corrupt/unopenable file returns FAILED status, never raises."""
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "broken.png")
        out = os.path.join(tmp, "broken.packshot.png")
        Path(src).write_bytes(b"not really a png \x00\x01\x02")

        res = normalize_image(src, out, name="BROKEN")

        assert res.status == NormalizeStatus.FAILED
        assert res.packshot_path is None
        assert res.features is None
        assert res.error and "open" in res.error
        assert not Path(out).exists()
        print(f"  corrupt -> FAILED ({res.error})")


def test_missing_file_returns_failed():
    res = normalize_image("/nonexistent/path.png", "/tmp/none.png", name="X")
    assert res.status == NormalizeStatus.FAILED
    assert res.features is None
    print("  missing file -> FAILED")


def test_has_real_alpha_detection():
    """Transparent blob -> True; fully-opaque RGBA -> False; RGB -> False."""
    transp = Image.new("RGBA", (50, 50), (0, 0, 0, 0))
    ImageDraw.Draw(transp).rectangle([10, 10, 40, 40], fill=(255, 0, 0, 255))
    assert has_real_alpha(transp) is True

    opaque_rgba = Image.new("RGBA", (50, 50), (10, 20, 30, 255))   # decorative alpha
    assert has_real_alpha(opaque_rgba) is False

    rgb = Image.new("RGB", (50, 50), (10, 20, 30))
    assert has_real_alpha(rgb) is False

    fully_transparent = Image.new("RGBA", (50, 50), (0, 0, 0, 0))  # nothing visible
    assert has_real_alpha(fully_transparent) is False
    print("  has_real_alpha: transparent=T opaque=F rgb=F empty=F")


def test_trim_to_content_is_tight():
    """trim_to_content removes empty margins; re-trimming is a no-op (idempotent)."""
    img = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
    ImageDraw.Draw(img).rectangle([100, 80, 200, 220], fill=(0, 255, 0, 255))
    trimmed = trim_to_content(img)
    assert trimmed.size == (101, 141)            # inclusive bbox of [100,80,200,220]
    assert trim_to_content(trimmed).size == trimmed.size
    print(f"  trim -> {trimmed.size} (idempotent)")


def test_extract_features_orientation_and_color():
    img = Image.new("RGBA", (300, 100), (0, 0, 0, 0))
    ImageDraw.Draw(img).rectangle([0, 0, 299, 99], fill=(200, 30, 30, 255))
    feats = extract_features(img, name="CAJA_KIT_1KG")
    assert feats.orientation == "landscape"
    assert feats.aspect_ratio == 3.0
    assert feats.dominant_color[0] > feats.dominant_color[1]   # red-dominant
    assert 0.95 <= feats.coverage <= 1.0
    assert feats.real_size_hint == 1.0                         # "1KG" -> large
    print(f"  features: {feats.orientation} ar={feats.aspect_ratio} "
          f"color={feats.dominant_color} cov={feats.coverage}")


def test_size_hint_none_without_keyword():
    img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    ImageDraw.Draw(img).rectangle([10, 10, 90, 90], fill=(50, 50, 50, 255))
    feats = extract_features(img, name="PRODUCTO_GENERICO")
    assert feats.real_size_hint is None
    print("  no keyword -> real_size_hint=None")


def test_bg_failure_does_not_pass_raw_through():
    """If bg removal raises on an opaque image, result is FAILED (no white box)."""
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "opaque.png")
        out = os.path.join(tmp, "opaque.packshot.png")
        Image.new("RGB", (120, 120), (255, 255, 255)).save(src)  # opaque, no alpha

        # Force remove_background to fail, simulating offline/missing model.
        orig = N.remove_background
        N.remove_background = lambda img, method: (_ for _ in ()).throw(
            RuntimeError("simulated rembg failure"))
        try:
            res = normalize_image(src, out, name="OPAQUE", bg_method=BgMethod.REMBG)
        finally:
            N.remove_background = orig

        assert res.status == NormalizeStatus.FAILED
        assert res.error and "bg" in res.error
        assert not Path(out).exists()            # crucially: NO raw passthrough
        print(f"  bg failure -> FAILED ({res.error}), no packshot written")


# ─── MODEL test (needs rembg + cached u2net) ──────────────────────────────────
def test_opaque_image_bg_removed_and_trimmed():
    """Opaque JPEG on white -> rembg cutout -> tight transparent packshot."""
    if not _rembg_available():
        print("  [SKIP] rembg model not cached (offline) — skipping model test")
        return
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "lata.jpg")
        out = os.path.join(tmp, "lata.packshot.png")
        _make_opaque_jpeg(src, 200, 200)

        res = normalize_image(src, out, name="LATA", bg_method=BgMethod.REMBG)

        assert res.status in (NormalizeStatus.OK, NormalizeStatus.DEGRADED), res.error
        assert res.method == BgMethod.REMBG
        assert Path(out).exists()
        packshot = Image.open(out)
        assert packshot.mode == "RGBA"
        assert has_real_alpha(packshot), "bg removal must yield real transparency"
        # Trimmed tighter than the original 200x200 frame.
        assert res.features.width <= 200 and res.features.height <= 200
        print(f"  opaque -> bg removed: {res.features.width}x{res.features.height} "
              f"conf={res.confidence} status={res.status.value}")


# ─── Plain runner (no pytest dependency) ──────────────────────────────────────
if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            print(f"• {t.__name__}")
            t()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"  FAIL: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ERROR: {type(e).__name__}: {e}")
    print(f"\n{passed} passed, {failed} failed (of {len(tests)})")
    raise SystemExit(1 if failed else 0)
