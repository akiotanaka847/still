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
    'auto':             _sort_auto,
    'area':             _sort_area,
    'alternado':        _sort_alternado,
    'centrado':         _sort_auto,   # sort igual, post-process centra
    'uniforme':         _sort_uniforme,
    'dinamico':         _sort_dinamico,
    'sombra':           _sort_auto,   # mismo orden que auto + sombra de contacto
    'profundidad':      _sort_auto,   # héroe al centro, solapamiento y sombra
    'profundidad_xl':   _sort_auto,   # héroe dominante (XL), más solapamiento
    'profundidad_grad': _sort_auto,   # escalonada: tamaño decrece del centro a los lados
    'ai':               list,         # IA: preserva el orden, plano
    'ai_depth':         list,         # IA + profundidad (preserva orden, centra héroe)
    'ai_shadow':        list,         # IA + sombra de contacto (preserva orden)
}

# Parámetros de las propuestas de profundidad
#   overlap: solapamiento horizontal | hero_scale: escala del producto central
#   falloff: factor de escala que se aplica por distancia al centro (1.0 = sin caída)
DEPTH_PARAMS = {
    'profundidad':      {'overlap': 0.16, 'hero_scale': 1.22, 'falloff': 1.00},
    'profundidad_xl':   {'overlap': 0.24, 'hero_scale': 1.50, 'falloff': 1.00},
    'profundidad_grad': {'overlap': 0.20, 'hero_scale': 1.18, 'falloff': 0.84},
    'ai_depth':         {'overlap': 0.16, 'hero_scale': 1.22, 'falloff': 1.00},
}


def compute_layout(
    images: List[dict],
    sort_strategy: str = 'auto',
    rows_count: int = 0,
    base_height: int = 760,
    item_gap: int = 40,
    internal_padding: int = 0,  # Siempre 0 — canvas exacto al contenido
    max_vertical_boost: float = 1.20,
    aspect_w: int = 0,
    aspect_h: int = 0,
    shadow: bool = False,
) -> dict:
    n = len(images)
    if n == 0:
        raise ValueError("No se proporcionaron imágenes")

    # Forzar padding = 0 para canvas exacto
    internal_padding = 0

    # ─── Efectos de las propuestas nuevas ────────────────────────────────────
    # Sombra: APAGADA por defecto (cutout limpio premium). El llamador la activa
    # explícitamente (intake B1 = Sutil/Marcada). Las estrategias 'sombra'/'ai_shadow'/
    # profundidad solo determinan la INTENSIDAD cuando la sombra está activa.
    depth = sort_strategy in DEPTH_PARAMS
    strong_shadow = shadow and (depth or sort_strategy in ('sombra', 'ai_shadow'))
    dp = DEPTH_PARAMS.get(sort_strategy, {})
    overlap = dp.get('overlap', 0.0)          # solapamiento horizontal
    hero_scale = dp.get('hero_scale', 1.0)    # escala del producto central
    depth_falloff = dp.get('falloff', 1.0)    # caída de tamaño hacia los lados
    # Sombra de contacto realista: fuerte en Sombra/Profundidad, sutil en el resto.
    # Con sombra apagada → 0 opacidad y 0 margen (canvas pegado al contenido).
    shadow_opacity = (0.28 if strong_shadow else 0.14) if shadow else 0.0
    shadow_blur = max(8, round(base_height * (0.05 if strong_shadow else 0.038)))
    shadow_margin = round(base_height * (0.12 if strong_shadow else 0.07)) if shadow else 0

    # Profundidad: una sola fila para que el solapamiento y el héroe tengan sentido
    if depth:
        rows_count = 1

    # Uniforme y dinámico modifican el boost
    if sort_strategy == 'uniforme':
        max_vertical_boost = 1.0
    elif sort_strategy == 'dinamico':
        max_vertical_boost = 1.6

    # Config automática — preferir 1 fila en sets pequeños (composición más limpia)
    if rows_count == 0 or aspect_w == 0:
        if n <= 6:
            rows_count = rows_count or 1; aspect_w = aspect_w or 5;  aspect_h = aspect_h or 3
        elif n <= 15:
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
        # Tamaño real relativo (lo aporta la IA; 1.0 = sin cambio). Acotado.
        rel = max(0.25, min(1.5, float(d.get("scale", 1.0))))
        if "scale" in d:
            # Estrategias IA: el 'size' controla el ÁREA VISUAL, no la altura.
            # Si se escalara la altura (como en las estrategias geométricas), un
            # producto ancho con el mismo 'size' ocuparía mucha más área y dominaría
            # la composición. Aquí fijamos area ∝ (base_height·boost·rel)² y la
            # repartimos entre ancho y alto preservando el aspect ratio con √(aspect).
            # Para productos cuadrados el resultado es idéntico al escalado por altura.
            linear = base_height * boost * rel       # lado equivalente (caso cuadrado)
            aspect = w / h                           # >1 ancho · <1 alto
            sh = max(1, round(linear / math.sqrt(aspect)))
            sw = max(1, round(linear * math.sqrt(aspect)))
        else:
            # Estrategias geométricas (sin 'size' de IA): escalado clásico por altura.
            sh = round(base_height * boost * rel)
            sw = round(sh * w / h)
        items.append(_Item(
            name=d.get("name", f"P{len(items)+1}"),
            filepath=d.get("filepath", ""),
            w=w, h=h, sw=sw, sh=sh,
        ))

    # Aplicar estrategia de ordenamiento
    sort_fn = SORT_STRATEGIES.get(sort_strategy, _sort_auto)
    items = sort_fn(items)

    # Distribuir en filas (balance por ancho aproximado)
    rows: List[_Row] = [_Row() for _ in range(rows_count)]
    for item in items:
        row = min(rows, key=lambda r: r.width)
        row.items.append(item)
        row.width += item.sw + item_gap
        if item.sh > row.height:
            row.height = item.sh

    # Post-proceso → el más alto al centro de la fila (silueta piramidal, más estética)
    if sort_strategy in ('centrado', 'auto', 'area') or depth:
        rows = _center_in_rows(rows)

    # Profundidad: escalar según la posición ya centrada
    #   héroe (el más alto, queda al centro) → hero_scale
    #   cada paso de índice hacia los lados → ×falloff
    row_hero = {}
    if depth:
        for ri, row in enumerate(rows):
            if not row.items:
                continue
            hero_idx = max(range(len(row.items)), key=lambda i: row.items[i].sh)
            row_hero[ri] = hero_idx
            for idx, item in enumerate(row.items):
                d = abs(idx - hero_idx)
                scale = hero_scale if d == 0 else (depth_falloff ** d)
                item.sw = max(1, round(item.sw * scale))
                item.sh = max(1, round(item.sh * scale))

    # Recalcular ancho/alto reales de cada fila (con o sin solapamiento)
    for row in rows:
        if not row.items:
            row.width = 0.0
            row.height = 0.0
            continue
        row.height = max(it.sh for it in row.items)
        if overlap > 0:
            # cada producto (salvo el último) avanza solo (1-overlap) de su ancho
            total = sum(it.sw for it in row.items)
            total -= overlap * sum(it.sw for it in row.items[:-1])
            row.width = total
        else:
            row.width = sum(it.sw for it in row.items) + item_gap * (len(row.items) - 1)

    # Tamaño del canvas — exacto al contenido
    widest = max(r.width for r in rows)

    # Calcular posiciones — sin offset, todo pegado al origen
    cy = 0.0
    out = []
    for ri, row in enumerate(rows):
        if not row.items:
            continue
        rx = (widest - row.width) / 2
        cx = rx
        hero_idx = row_hero.get(ri, (len(row.items) - 1) / 2.0)
        for idx, item in enumerate(row.items):
            item.x = cx
            item.y = cy + (row.height - item.sh)   # alineados a la base
            # z-order: en profundidad, el héroe (y lo cercano a él) queda al frente
            z = (-abs(idx - hero_idx)) if depth else float(idx)
            if overlap > 0:
                cx += item.sw * (1 - overlap)
            else:
                cx += item.sw + item_gap
            out.append({
                "name":     item.name,
                "filepath": item.filepath,
                "ow": item.w, "oh": item.h,
                "sw": item.sw, "sh": item.sh,
                "x":  round(item.x), "y": round(item.y),
                "z":  round(z, 3),
            })
        cy += row.height + shadow_margin + item_gap

    # Alto: conserva el margen de sombra de la última fila, descarta el gap final
    doc_w = math.ceil(widest)
    doc_h = math.ceil(cy - item_gap)

    doc_w = min(doc_w, 30000)
    doc_h = min(doc_h, 30000)

    return {
        "canvas_width":  doc_w,
        "canvas_height": doc_h,
        "rows_count":    rows_count,
        "sort_strategy": sort_strategy,
        "items":         out,
        "effects": {
            "shadow":         shadow,
            "depth":          depth,
            "shadow_opacity": shadow_opacity,
            "shadow_blur":    shadow_blur,
            "shadow_margin":  shadow_margin,
        },
    }
