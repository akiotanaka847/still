"""
Exportador de PSD por CAPAS para Still Studio.

Toma el mismo `layout` que usa `renderer.render_png` y, en vez de aplanar todo en
un PNG, escribe un .psd donde **cada producto es su propia capa editable**, más una
capa de Sombras y (si aplica) una de Fondo. Así el diseñador abre el archivo en
Photoshop y mueve / escala / oculta / reemplaza cada producto por separado.

Orden de capas (de arriba hacia abajo): productos (por z, el héroe al frente),
luego "Sombras", luego "Fondo". Cada capa de producto se nombra con el producto.

Nota de honestidad: las fotos de producto son raster (píxeles), no vectores. Estas
capas son raster de alta resolución; en Photoshop el diseñador puede convertir
cualquiera a Smart Object con un clic (clic derecho → "Convertir en objeto
inteligente") para escalado no destructivo. No vectorizamos la foto (eso arruinaría
la calidad fotográfica).

Requiere: pytoshop (escritor PSD) + numpy. Compresión RAW (la RLE de pytoshop 1.2.1
está rota). Python 3.9 compatible.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
from PIL import Image

import pytoshop
from pytoshop.user import nested_layers
from pytoshop.enums import ColorMode, Compression

from renderer import _load_trimmed, _draw_silhouette_shadow


def _channels(rgba: Image.Image) -> Dict[int, np.ndarray]:
    """RGBA PIL → dict de canales pytoshop {0:R,1:G,2:B,-1:A} como uint8 2D."""
    a = np.asarray(rgba.convert("RGBA"), dtype=np.uint8)
    return {0: a[:, :, 0], 1: a[:, :, 1], 2: a[:, :, 2], -1: a[:, :, 3]}


def _image_layer(name: str, rgba: Image.Image, top: int, left: int) -> nested_layers.Image:
    """Construye una capa de imagen pytoshop posicionada en (top,left)."""
    h, w = rgba.height, rgba.width
    return nested_layers.Image(
        name=name,
        top=int(top), left=int(left), bottom=int(top + h), right=int(left + w),
        channels=_channels(rgba),
    )


def export_psd(layout: dict, output_path: str, background_path: Optional[str] = None) -> str:
    """
    Escribe un PSD por capas a `output_path`. Devuelve la ruta escrita.

    Reusa exactamente la geometría de `layout` (mismas posiciones/escala que el PNG),
    así el PSD y el PNG coinciden pixel a pixel cuando se aplanan.
    """
    cw = int(layout["canvas_width"])
    ch = int(layout["canvas_height"])

    effects = layout.get("effects", {})
    shadow_on = bool(effects.get("shadow"))
    shadow_opacity = float(effects.get("shadow_opacity", 0.0))
    shadow_blur = int(effects.get("shadow_blur", 12))

    items = sorted(layout["items"], key=lambda it: it.get("z", 0))

    # Precargar productos (recortados + escalados) una vez.
    loaded = []  # (item, img)
    for item in items:
        fp = item.get("filepath", "")
        if not fp or not Path(fp).exists():
            continue
        img = _load_trimmed(fp).resize((item["sw"], item["sh"]), Image.LANCZOS)
        loaded.append((item, img))

    # pytoshop ordena la lista de ARRIBA hacia abajo (primero = capa superior).
    layers: List[nested_layers.Image] = []

    # Productos: el de mayor z al frente (arriba en la lista).
    for item, img in sorted(loaded, key=lambda li: li[0].get("z", 0), reverse=True):
        layers.append(_image_layer(str(item.get("name", "producto")), img, item["y"], item["x"]))

    # Sombras: una sola capa de canvas completo con todas las sombras de silueta.
    if shadow_on:
        shadow_canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        for item, img in loaded:
            try:
                _draw_silhouette_shadow(shadow_canvas, item, img, shadow_opacity, shadow_blur)
            except Exception:
                pass
        if shadow_canvas.getbbox():  # solo si hay algo dibujado
            layers.append(_image_layer("Sombras", shadow_canvas, 0, 0))

    # Fondo: capa de canvas completo (abajo del todo).
    if background_path and Path(background_path).exists():
        bg = Image.open(background_path).convert("RGBA").resize((cw, ch), Image.LANCZOS)
        layers.append(_image_layer("Fondo", bg, 0, 0))

    psd = nested_layers.nested_layers_to_psd(
        layers, color_mode=ColorMode.rgb, compression=Compression.raw,
        # OJO: pese a que el docstring de pytoshop dice (height, width), su código
        # desempaqueta `width, height = size` → hay que pasar (width, height).
        size=(cw, ch),
    )
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        psd.write(f)
    return output_path
