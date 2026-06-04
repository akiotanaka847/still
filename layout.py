"""
Motor de layout para bodegones.
Soporta 6 estrategias de ordenamiento/escalado.
"""
import math
from dataclasses import dataclass, field
from typing import List


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


# ─── Estrategias de ordenamiento ─────────────────────────────────────────────

def _sort_auto(items: List[_Item]) -> List[_Item]:
    return sorted(items, key=lambda x: x.sh, reverse=True)

def _sort_area(items: List[_Item]) -> List[_Item]:
    return sorted(items, key=lambda x: x.w * x.h, reverse=True)

def _sort_alternado(items: List[_Item]) -> List[_Item]:
    """Intercala productos verticales y horizontales para ritmo visual."""
    by_ratio = sorted(items, key=lambda x: x.h / max(x.w, 1), reverse=True)
    tall = [x for i, x in enumerate(by_ratio) if i % 2 == 0]
    wide = [x for i, x in enumerate(by_ratio) if i % 2 != 0]
    result = []
    for i in range(max(len(tall), len(wide))):
        if i < len(tall): result.append(tall[i])
        if i < len(wide): result.append(wide[i])
    return result

def _sort_uniforme(items: List[_Item]) -> List[_Item]:
    return sorted(items, key=lambda x: x.w, reverse=True)

def _sort_dinamico(items: List[_Item]) -> List[_Item]:
    return sorted(items, key=lambda x: x.sh, reverse=True)


def _center_in_rows(rows: List[_Row]) -> List[_Row]:
    """Reordena items dentro de cada fila para que el más alto quede al centro."""
    for row in rows:
        if len(row.items) < 3:
            continue
        s = sorted(row.items, key=lambda x: x.sh, reverse=True)
        res: list = [None] * len(s)
        mid = len(s) // 2
        res[mid] = s[0]
        l, r = mid - 1, mid + 1
        for i in range(1, len(s)):
            if i % 2 == 1 and r < len(s):
                res[r] = s[i]; r += 1
            elif l >= 0:
                res[l] = s[i]; l -= 1
            else:
                res[r] = s[i]; r += 1
        row.items = [x for x in res if x is not None]
    return rows


SORT_STRATEGIES = {
    'auto':      _sort_auto,
    'area':      _sort_area,
    'alternado': _sort_alternado,
    'centrado':  _sort_auto,      # sort igual, post-process centra
    'uniforme':  _sort_uniforme,
    'dinamico':  _sort_dinamico,
    'ai':        list,            # preserva el orden decidido por la IA
}


def compute_layout(
    images: List[dict],
    sort_strategy: str = 'auto',
    rows_count: int = 0,
    base_height: int = 760,
    item_gap: int = 40,
    internal_padding: int = 120,
    max_vertical_boost: float = 1.20,
    aspect_w: int = 0,
    aspect_h: int = 0,
) -> dict:
    n = len(images)
    if n == 0:
        raise ValueError("No se proporcionaron imágenes")

    # Uniforme y dinámico modifican el boost
    if sort_strategy == 'uniforme':
        max_vertical_boost = 1.0
    elif sort_strategy == 'dinamico':
        max_vertical_boost = 1.6

    # Config automática
    if rows_count == 0 or aspect_w == 0:
        if n <= 3:
            rows_count = rows_count or 1; aspect_w = aspect_w or 5;  aspect_h = aspect_h or 3
        elif n <= 8:
            rows_count = rows_count or 2; aspect_w = aspect_w or 5;  aspect_h = aspect_h or 3
        elif n <= 20:
            rows_count = rows_count or 2; aspect_w = aspect_w or 8;  aspect_h = aspect_h or 3
        else:
            rows_count = rows_count or 3; aspect_w = aspect_w or 10; aspect_h = aspect_h or 3
    aspect_h = aspect_h or 3

    # Escalar items
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
            name=d.get("name", f"P{len(items)+1}"),
            filepath=d.get("filepath", ""),
            w=w, h=h, sw=sw, sh=sh,
        ))

    # Aplicar estrategia de ordenamiento
    sort_fn = SORT_STRATEGIES.get(sort_strategy, _sort_auto)
    items = sort_fn(items)

    # Distribuir en filas
    rows: List[_Row] = [_Row() for _ in range(rows_count)]
    for item in items:
        row = min(rows, key=lambda r: r.width)
        row.items.append(item)
        row.width += item.sw + item_gap
        if item.sh > row.height:
            row.height = item.sh

    # Post-proceso "centrado"
    if sort_strategy == 'centrado':
        rows = _center_in_rows(rows)

    # Tamaño del canvas — exacto al contenido, sin padding
    widest = max(r.width for r in rows)
    total_h = sum(r.height for r in rows)

    # Canvas sin padding — tamaño exacto del contenido
    # Restar el gap final de cada fila (no debe ocupar ancho)
    doc_w = math.ceil(widest - item_gap)  # Último gap no cuenta
    doc_h = math.ceil(total_h)

    doc_w = min(doc_w, 30000)
    doc_h = min(doc_h, 30000)

    # Calcular posiciones — sin offset, todo pegado al origen
    gx = 0.0  # Sin centrado horizontal
    cy = 0.0  # Sin padding vertical
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
                "ow": item.w, "oh": item.h,
                "sw": item.sw, "sh": item.sh,
                "x":  round(item.x), "y": round(item.y),
            })
        cy += row.height + item_gap

    return {
        "canvas_width":  doc_w,
        "canvas_height": doc_h,
        "rows_count":    rows_count,
        "sort_strategy": sort_strategy,
        "items":         out,
    }
