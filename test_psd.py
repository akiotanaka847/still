"""
Test del exportador PSD por capas (psd_export.py).

Verifica: cada producto en su propia capa (por nombre), capa "Sombras" cuando hay
sombra, y que el canvas del PSD coincide con el del PNG (mismo layout). Usa imágenes
sintéticas transparentes (no requiere rembg).

Run: .venv-test/bin/python test_psd.py
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from layout import compute_layout
from psd_export import export_psd


def _prod(path, w, h, color):
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    m = int(min(w, h) * 0.15)
    ImageDraw.Draw(img).rounded_rectangle([m, m, w - m, h - m], radius=12, fill=color)
    img.save(path)


def _layout(tmp, shadow):
    p1, p2 = Path(tmp) / "BOTELLA.png", Path(tmp) / "FRASCO.png"
    _prod(p1, 240, 620, (40, 90, 160, 255))
    _prod(p2, 360, 360, (180, 60, 90, 255))
    images = [
        {"name": "BOTELLA", "filepath": str(p1), "width": 240, "height": 620, "scale": 1.0},
        {"name": "FRASCO", "filepath": str(p2), "width": 360, "height": 360, "scale": 0.6},
    ]
    return compute_layout(images=images, sort_strategy="ai_depth", base_height=400, shadow=shadow)


def _layer_names(psd_path):
    from psd_tools import PSDImage
    p = PSDImage.open(psd_path)
    return p.size, [layer.name.rstrip("\x00") for layer in p]


def test_each_product_is_its_own_layer():
    with tempfile.TemporaryDirectory() as tmp:
        layout = _layout(tmp, shadow=False)
        out = str(Path(tmp) / "bodegon.psd")
        export_psd(layout, out)
        size, names = _layer_names(out)
        assert "BOTELLA" in names and "FRASCO" in names, names
        # sin sombra → no hay capa Sombras
        assert "Sombras" not in names, names
        # canvas del PSD = canvas del layout (w, h)
        assert size == (layout["canvas_width"], layout["canvas_height"]), (size, layout["canvas_width"], layout["canvas_height"])
        print(f"  sin sombra → capas {names} canvas {size}")


def test_shadow_layer_present_when_enabled():
    with tempfile.TemporaryDirectory() as tmp:
        layout = _layout(tmp, shadow=True)
        out = str(Path(tmp) / "bodegon.psd")
        export_psd(layout, out)
        _, names = _layer_names(out)
        assert "Sombras" in names, names
        assert "BOTELLA" in names and "FRASCO" in names, names
        print(f"  con sombra → capas {names}")


def test_background_layer_present():
    with tempfile.TemporaryDirectory() as tmp:
        bg = Path(tmp) / "fondo.png"
        Image.new("RGBA", (50, 50), (220, 220, 220, 255)).save(bg)
        layout = _layout(tmp, shadow=False)
        out = str(Path(tmp) / "bodegon.psd")
        export_psd(layout, out, background_path=str(bg))
        _, names = _layer_names(out)
        assert "Fondo" in names, names
        print(f"  con fondo → capas {names}")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            print(f"• {t.__name__}")
            t(); passed += 1
        except Exception as e:  # noqa: BLE001
            failed += 1; print(f"  FAIL: {type(e).__name__}: {e}")
    print(f"\n{passed} passed, {failed} failed (of {len(tests)})")
    raise SystemExit(1 if failed else 0)
