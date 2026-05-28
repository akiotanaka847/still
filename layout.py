"""
Motor de layout para bodegones — portado de Bodegones2.jsx.
Calcula escala perceptual y distribución en filas.
"""
import math
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class _Item:
    name: str
    filepath: str
    w: int
    h: int
    sw: int = 0
    sh: int = 0
    x: float = 0.0
    y: float = 0.0


@dataclass
class _Row:
    items: List[_Item] = field(default_factory=list)
    width: float = 0.0
    height: float = 0.0


def compute_layout(
    images: List[dict],
    rows_count: int = 0,
    base_height: int = 760,
    item_gap: int = 40,
    internal_padding: int = 120,
    max_vertical_boost: float = 1.20,
    aspect_w: int = 0,
    aspect_h: int = 0,
) -> dict:
    """
    Calcula el layout de bodegón para una lista de imágenes.

    images: [{"name": str, "filepath": str, "width": int, "height": int}]
    Returns: {canvas_width, canvas_height, rows_count, items: [...]}
    """
    n = len(images)
    if n == 0:
        raise ValueError("No se proporcionaron imágenes")

    # Config automática según cantidad de productos
    if rows_count == 0 or aspect_w == 0:
        if n <= 15:
            rows_count = rows_count or 2
            aspect_w = aspect_w or 5
            aspect_h = aspect_h or 3
        elif n <= 30:
            rows_count = rows_count or 2
            aspect_w = aspect_w or 8
            aspect_h = aspect_h or 3
        else:
            rows_count = rows_count or 3
            aspect_w = aspect_w or 10
            aspect_h = aspect_h or 3

    aspect_h = aspect_h or 3

    # Escala con boost para verticales
    items: List[_Item] = []
    for d in images:
        w = max(int(d.get("width", 1)), 1)
        h = max(int(d.get("height", 1)), 1)
        ratio = h / w

        boost = 1.0
        if ratio > 1.35:
            boost = 1.0 + (max_vertical_boost - 1.0) * min(ratio / 3.0, 1.0)

        sh = round(base_height * boost)
        sw = round(sh * w / h)

        items.append(_Item(
            name=d.get("name", f"PRODUCTO_{len(items)+1}"),
            filepath=d.get("filepath", ""),
            w=w, h=h, sw=sw, sh=sh,
        ))

    # Ordenar por altura descendente
    items.sort(key=lambda x: x.sh, reverse=True)

    # Crear filas y distribuir (fila con menor ancho acumula)
    rows: List[_Row] = [_Row() for _ in range(rows_count)]
    for item in items:
        row = min(rows, key=lambda r: r.width)
        row.items.append(item)
        row.width += item.sw + item_gap
        if item.sh > row.height:
            row.height = item.sh

    # Tamaño del canvas
    widest = max(r.width for r in rows)
    total_h = sum(r.height for r in rows)

    doc_h = total_h + internal_padding * 2
    doc_w = doc_h * (aspect_w / aspect_h)
    min_w = widest + internal_padding * 2
    if min_w > doc_w:
        doc_w = min_w

    doc_w = min(math.ceil(doc_w), 30000)
    doc_h = min(math.ceil(doc_h), 30000)

    # Calcular posiciones (alineación base inferior por fila)
    gx = (doc_w - widest) / 2
    cy = float(internal_padding)
    out = []

    for row in rows:
        rx = (widest - row.width) / 2
        cx = gx + rx
        for item in row.items:
            item.x = cx
            item.y = cy + (row.height - item.sh)
            cx += item.sw + item_gap
            out.append({
                "name":     item.name,
                "filepath": item.filepath,
                "ow":       item.w,
                "oh":       item.h,
                "sw":       item.sw,
                "sh":       item.sh,
                "x":        round(item.x),
                "y":        round(item.y),
            })
        cy += row.height + item_gap

    return {
        "canvas_width":  doc_w,
        "canvas_height": doc_h,
        "rows_count":    rows_count,
        "items":         out,
    }
