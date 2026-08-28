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
    'impacto':          _sort_auto,   # héroe al centro + abanico de inclinaciones
    'ai_impacto':       list,         # IA + impacto (preserva orden, centra héroe)
    'fila_recta':       _sort_auto,   # derechos en línea, sin héroe centrado
    'ai_fila_recta':    list,         # IA + fila recta (preserva orden)
    'cruce':            _sort_auto,   # inclinados unos hacia otros (convergen al centro)
    'ai_cruce':         list,         # IA + cruce (preserva orden)
    'inclinado_derecha':    _sort_auto,   # todos inclinados parejo hacia la derecha (ángulo configurable)
    'ai_inclinado_derecha': list,
    'inclinado_izquierda':    _sort_auto,  # todos inclinados parejo hacia la izquierda (ángulo configurable)
    'ai_inclinado_izquierda': list,
}

# Parámetros de las propuestas de profundidad
#   overlap: solapamiento horizontal | hero_scale: escala del producto central
#   falloff: factor de escala que se aplica por distancia al centro (1.0 = sin caída)
#   tilt: si True, cada producto se inclina según su distancia al pivote
#   tilt_mode: 'fan' = abanico hacia afuera desde el héroe (impacto)
#              'converge' = inclinación hacia el centro de la fila (cruce)
#              'uniform' = mismo ángulo fijo para TODOS, sin fórmula por distancia
#   tilt_angle: solo para tilt_mode='uniform' — grados fijos (+ o -) para todos
#   center_hero: si False, no reordena para centrar al más alto (deja el orden natural)
DEPTH_PARAMS = {
    # hero_scale/falloff son el ÚNICO factor de diferenciación visual cuando
    # los productos tienen tamaño real muy parecido (p. ej. varios snacks de
    # ~40g) — ahí la IA no tiene de dónde sacar una diferencia de tamaño
    # "real", así que el % de boost tiene que notarse por sí solo. Valores
    # subidos (antes profundidad=1.22/1.00, impacto=1.35/0.92) para que la
    # jerarquía se vea aunque el origen sea prácticamente plano.
    'profundidad':      {'overlap': 0.16, 'hero_scale': 1.32, 'falloff': 0.93},
    'profundidad_xl':   {'overlap': 0.24, 'hero_scale': 1.50, 'falloff': 1.00},
    'profundidad_grad': {'overlap': 0.20, 'hero_scale': 1.18, 'falloff': 0.84},
    'ai_depth':         {'overlap': 0.16, 'hero_scale': 1.32, 'falloff': 0.93},
    'impacto':          {'overlap': 0.22, 'hero_scale': 1.50, 'falloff': 0.88, 'tilt': True, 'tilt_mode': 'fan'},
    'ai_impacto':       {'overlap': 0.22, 'hero_scale': 1.50, 'falloff': 0.88, 'tilt': True, 'tilt_mode': 'fan'},
    'fila_recta':       {'overlap': 0.16, 'hero_scale': 1.00, 'falloff': 1.00, 'center_hero': False},
    'ai_fila_recta':    {'overlap': 0.16, 'hero_scale': 1.00, 'falloff': 1.00, 'center_hero': False},
    'cruce':            {'overlap': 0.32, 'hero_scale': 1.15, 'falloff': 1.00, 'tilt': True, 'tilt_mode': 'converge'},
    'ai_cruce':         {'overlap': 0.32, 'hero_scale': 1.15, 'falloff': 1.00, 'tilt': True, 'tilt_mode': 'converge'},
    # tilt_angle acá es el DEFAULT si no viene 'tilt_angle' en compute_layout()
    # (el usuario lo elige con un slider en la UI) — el signo define la
    # dirección (derecha/izquierda), la magnitud la pisa lo que mande el usuario.
    'inclinado_derecha':    {'overlap': 0.16, 'hero_scale': 1.00, 'falloff': 1.00, 'center_hero': False,
                              'tilt': True, 'tilt_mode': 'uniform', 'tilt_angle': 30.0},
    'ai_inclinado_derecha': {'overlap': 0.16, 'hero_scale': 1.00, 'falloff': 1.00, 'center_hero': False,
                              'tilt': True, 'tilt_mode': 'uniform', 'tilt_angle': 30.0},
    'inclinado_izquierda':    {'overlap': 0.16, 'hero_scale': 1.00, 'falloff': 1.00, 'center_hero': False,
                                'tilt': True, 'tilt_mode': 'uniform', 'tilt_angle': -30.0},
    'ai_inclinado_izquierda': {'overlap': 0.16, 'hero_scale': 1.00, 'falloff': 1.00, 'center_hero': False,
                                'tilt': True, 'tilt_mode': 'uniform', 'tilt_angle': -30.0},
}

# Inclinación del abanico en "impacto": grados por producto según distancia al héroe
TILT_BASE = 20.0   # inclinación del primer producto a cada lado del héroe
TILT_STEP = 10.0   # incremento por cada paso adicional de distancia
TILT_MAX = 30.0    # tope (los productos más externos llegan a este máximo)


def _scale_items(images: List[dict], base_height: int, max_vertical_boost: float = 1.20) -> List[_Item]:
    """Escala cada imagen a su tamaño base (según proporción y el 'scale'
    relativo que aporta la IA). Compartido por compute_layout() y los layouts
    standalone (cascada, radial) para no duplicar la lógica de escalado."""
    items: List[_Item] = []
    for d in images:
        w = max(int(d.get("width", 1)), 1)
        h = max(int(d.get("height", 1)), 1)
        ratio = h / w
        boost = 1.0
        if ratio > 1.35:
            boost = 1.0 + (max_vertical_boost - 1.0) * min(ratio / 3.0, 1.0)
        rel = max(0.25, min(1.5, float(d.get("scale", 1.0))))
        sh = round(base_height * boost * rel)
        sw = round(sh * w / h)
        items.append(_Item(
            name=d.get("name", f"P{len(items)+1}"),
            filepath=d.get("filepath", ""),
            w=w, h=h, sw=sw, sh=sh,
        ))
    return items


def _rotated_bbox(w: float, h: float, degrees: float) -> tuple:
    """Ancho/alto del recuadro que contiene a (w,h) rotado 'degrees' grados."""
    rad = math.radians(abs(degrees))
    new_w = w * abs(math.cos(rad)) + h * abs(math.sin(rad))
    new_h = w * abs(math.sin(rad)) + h * abs(math.cos(rad))
    return new_w, new_h


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
    tilt_angle: float = None,  # solo tilt_mode='uniform' (Inclinado ★): magnitud en grados
                                # elegida por el usuario; el signo lo define la estrategia
                                # (derecha/izquierda). None = usa el default de DEPTH_PARAMS.
) -> dict:
    n = len(images)
    if n == 0:
        raise ValueError("No se proporcionaron imágenes")

    # Forzar padding = 0 para canvas exacto
    internal_padding = 0

    # ─── Efectos de las propuestas nuevas ────────────────────────────────────
    depth = sort_strategy in DEPTH_PARAMS
    strong_shadow = depth or sort_strategy in ('sombra', 'ai_shadow')
    shadow = True                              # grounding sutil en TODAS las propuestas
    dp = DEPTH_PARAMS.get(sort_strategy, {})
    overlap = dp.get('overlap', 0.0)          # solapamiento horizontal
    hero_scale = dp.get('hero_scale', 1.0)    # escala del producto central
    depth_falloff = dp.get('falloff', 1.0)    # caída de tamaño hacia los lados
    tilt = bool(dp.get('tilt', False))        # abanico/convergencia de inclinaciones
    tilt_mode = dp.get('tilt_mode', 'fan')    # 'fan' (impacto) | 'converge' (cruce)
    # Sombra de contacto: fuerte en Sombra/Profundidad, sutil en el resto
    shadow_opacity = 0.30 if strong_shadow else 0.16
    shadow_blur = max(8, round(base_height * (0.045 if strong_shadow else 0.034)))
    shadow_margin = round(base_height * (0.12 if strong_shadow else 0.07))

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
    items: List[_Item] = _scale_items(images, base_height, max_vertical_boost)

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
    # 'fila_recta' desactiva esto con center_hero=False: deja el orden natural,
    # sin pirámide (así se ven "en línea", como una foto de producto convencional).
    center_hero = dp.get('center_hero', True)
    if sort_strategy in ('centrado', 'auto', 'area') or (depth and center_hero):
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
            # Inclinación: en modo 'fan' (impacto) el héroe queda recto y los
            # laterales se inclinan hacia AFUERA desde su índice. En modo
            # 'converge' (cruce) se inclinan hacia el CENTRO GEOMÉTRICO de la
            # fila (no del héroe), para que converjan entre sí aunque no haya
            # un solo producto "recto" de referencia (p. ej. con 2 productos).
            # En modo 'uniform' TODOS —incluido el que sería héroe— llevan el
            # mismo ángulo fijo (tilt_angle): sin fórmula por distancia.
            rotation = 0.0
            if tilt:
                if tilt_mode == 'uniform':
                    default_angle = dp.get('tilt_angle', 30.0)
                    if tilt_angle is not None:
                        # El usuario da la MAGNITUD (siempre positiva desde el
                        # slider); el signo (derecha/izquierda) lo define la
                        # estrategia elegida, no lo que tipeó el usuario.
                        sign = 1.0 if default_angle >= 0 else -1.0
                        rotation = sign * abs(float(tilt_angle))
                    else:
                        rotation = default_angle
                else:
                    pivot = hero_idx if tilt_mode == 'fan' else (len(row.items) - 1) / 2.0
                    d = idx - pivot
                    if d != 0:
                        mag = min(TILT_MAX, TILT_BASE + TILT_STEP * (abs(d) - 1))
                        sign = 1 if d > 0 else -1
                        if tilt_mode == 'converge':
                            sign = -sign
                        rotation = sign * mag
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
                "rotation": round(rotation, 2),
            })
        cy += row.height + shadow_margin + item_gap

    # Alto: conserva el margen de sombra de la última fila, descarta el gap final
    doc_w = math.ceil(widest)
    doc_h = math.ceil(cy - item_gap)

    # La inclinación hace que las esquinas del producto sobresalgan de su caja
    # original. Se calcula cuánto crece cada caja al rotar (bbox real, no una
    # estimación) y se usa el máximo como margen, para que ningún ángulo —ni
    # los cercanos al tope de 30°— recorte el producto contra el borde.
    if tilt:
        margin_x = margin_y = 0.0
        for it in out:
            rot = it.get("rotation", 0.0)
            if not rot:
                continue
            new_w, new_h = _rotated_bbox(it["sw"], it["sh"], rot)
            margin_x = max(margin_x, (new_w - it["sw"]) / 2)
            margin_y = max(margin_y, (new_h - it["sh"]) / 2)
        margin_x = math.ceil(margin_x) + 4
        margin_y = math.ceil(margin_y) + 4
        for it in out:
            it["x"] += margin_x
            it["y"] += margin_y
        doc_w += margin_x * 2
        doc_h += margin_y * 2

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
            "tilt":           tilt,
            "shadow_opacity": shadow_opacity,
            "shadow_blur":    shadow_blur,
            "shadow_margin":  shadow_margin,
        },
    }


# ─── Layouts standalone (no encajan en el modelo de filas/héroe) ──────────────

def compute_layout_cascada(
    images: List[dict],
    base_height: int = 760,
    max_vertical_boost: float = 1.20,
    growth: float = 1.28,
    overlap: float = 0.28,
    tilt_deg: float = -14.0,
    step_up: float = 0.32,
    **_ignored,
) -> dict:
    """Cascada diagonal: todos los productos inclinados en la MISMA dirección
    (no en abanico), creciendo en tamaño hacia una esquina, solapados —
    transmite flujo/movimiento en vez de simetría. Pensado para una línea de
    producto (mismo SKU en variantes) más que para un héroe puntual."""
    n = len(images)
    if n == 0:
        raise ValueError("No se proporcionaron imágenes")

    base_items = _scale_items(images, base_height, max_vertical_boost)
    order = sorted(range(n), key=lambda i: base_items[i].sh)  # menor a mayor

    prepared = []
    g = 1.0
    for i in order:
        it = base_items[i]
        sh = round(it.sh * g)
        sw = round(sh * it.w / it.h)
        prepared.append({"item": it, "sw": sw, "sh": sh})
        g *= growth

    xs, x = [], 0.0
    for p in prepared:
        xs.append(x)
        x += p["sw"] * (1 - overlap)

    ys, cursor_y = [], 0.0
    for i, p in enumerate(prepared):
        if i > 0:
            cursor_y -= p["sh"] * step_up
        ys.append(cursor_y)
    min_y = min(ys)

    margin = round(base_height * 0.14)
    out = []
    max_right = max_bottom = 0.0
    for i, p in enumerate(prepared):
        it, sw, sh = p["item"], p["sw"], p["sh"]
        x0 = xs[i] + margin
        y0 = (ys[i] - min_y) + margin
        rw, rh = _rotated_bbox(sw, sh, tilt_deg)
        out.append({
            "name": it.name, "filepath": it.filepath,
            "ow": it.w, "oh": it.h, "sw": sw, "sh": sh,
            "x": round(x0), "y": round(y0),
            "z": float(i),
            "rotation": round(tilt_deg, 2),
        })
        max_right = max(max_right, x0 + rw / 2 + sw / 2)
        max_bottom = max(max_bottom, y0 + rh / 2 + sh / 2)

    doc_w = min(math.ceil(max_right) + margin, 30000)
    doc_h = min(math.ceil(max_bottom) + margin, 30000)

    return {
        "canvas_width": doc_w, "canvas_height": doc_h,
        "rows_count": 1, "sort_strategy": "cascada", "items": out,
        "effects": {
            "shadow": True, "depth": True, "tilt": True,
            "shadow_opacity": 0.30,
            "shadow_blur": max(8, round(base_height * 0.045)),
            "shadow_margin": round(base_height * 0.12),
        },
    }


def compute_layout_radial(
    images: List[dict],
    base_height: int = 760,
    max_vertical_boost: float = 1.20,
    overlap: float = -0.06,   # negativo = separación en vez de solapamiento
    max_tilt: float = 22.0,
    height_jitter: float = 0.12,
    **_ignored,
) -> dict:
    """Radial/Explosión: alturas y ángulos variados por posición — ninguno
    sobre una única línea de base, como si irradiaran desde un punto central.
    Pensado para grupos de 3+ productos distintos entre sí."""
    n = len(images)
    if n == 0:
        raise ValueError("No se proporcionaron imágenes")

    items = _scale_items(images, base_height, max_vertical_boost)
    order = sorted(range(n), key=lambda i: items[i].sh, reverse=True)
    mid = (n - 1) / 2.0

    prepared = []
    for rank, i in enumerate(order):
        it = items[i]
        d = rank - mid
        # Alterna arriba/abajo según la paridad de la distancia al centro,
        # así no queda un semicírculo parejo sino más orgánico.
        yfrac = (height_jitter if rank % 2 == 0 else -height_jitter) * (abs(d) / max(mid, 1))
        rot = 0.0 if abs(d) < 1e-6 else max(-max_tilt, min(max_tilt, d * (max_tilt / max(mid, 1))))
        prepared.append({"item": it, "yfrac": yfrac, "rotation": rot})

    # Mismo criterio que el motor de filas: overlap>0 solapa (avanza menos que
    # sw), overlap<0 separa (avanza más que sw). Por defecto Radial separa un
    # poco (overlap=-0.06) para que se lean como productos distintos irradiando.
    xs, x = [], 0.0
    for p in prepared:
        xs.append(x)
        x += p["item"].sw * (1 - overlap)

    max_h = max(p["item"].sh for p in prepared)
    margin = round(base_height * 0.18)

    out = []
    max_right = max_bottom = 0.0
    min_top = 0.0
    ys = []
    for p in prepared:
        it = p["item"]
        y = (max_h - it.sh) / 2 + p["yfrac"] * max_h
        ys.append(y)
        min_top = min(min_top, y)

    for i, p in enumerate(prepared):
        it = p["item"]
        rot = p["rotation"]
        x0 = xs[i] + margin
        y0 = (ys[i] - min_top) + margin
        rw, rh = _rotated_bbox(it.sw, it.sh, rot) if rot else (it.sw, it.sh)
        out.append({
            "name": it.name, "filepath": it.filepath,
            "ow": it.w, "oh": it.h, "sw": it.sw, "sh": it.sh,
            "x": round(x0), "y": round(y0),
            "z": -abs(i - (len(prepared) - 1) / 2.0),
            "rotation": round(rot, 2),
        })
        max_right = max(max_right, x0 + rw / 2 + it.sw / 2)
        max_bottom = max(max_bottom, y0 + rh / 2 + it.sh / 2)

    doc_w = min(math.ceil(max_right) + margin, 30000)
    doc_h = min(math.ceil(max_bottom) + margin, 30000)

    return {
        "canvas_width": doc_w, "canvas_height": doc_h,
        "rows_count": 1, "sort_strategy": "radial", "items": out,
        "effects": {
            "shadow": True, "depth": True, "tilt": True,
            "shadow_opacity": 0.28,
            "shadow_blur": max(8, round(base_height * 0.04)),
            "shadow_margin": round(base_height * 0.10),
        },
    }


FLOAT_AMPLITUDE = 0.30   # cuánto puede subir/bajar cada producto (fracción de la altura máx.)
FLOAT_TILT_MAX = 8.0     # inclinación leve para que se sienta orgánico, no una fila prolija


def compute_layout_flotante(
    images: List[dict],
    base_height: int = 760,
    max_vertical_boost: float = 1.20,
    overlap: float = -0.04,
    **_ignored,
) -> dict:
    """Flotante: cada producto a su propia altura (sin línea de base
    compartida) e inclinación leve, como si flotaran a distintas distancias
    del piso. La sombra de contacto se achica/desvanece según qué tan
    'lejos' flota cada uno (ver float_drop → renderer._draw_contact_shadow)."""
    n = len(images)
    if n == 0:
        raise ValueError("No se proporcionaron imágenes")

    items = _scale_items(images, base_height, max_vertical_boost)
    order = sorted(range(n), key=lambda i: items[i].sh, reverse=True)

    prepared = []
    for rank, i in enumerate(order):
        it = items[i]
        # Onda determinística (ángulo ~dorado) para que las alturas no caigan
        # en un patrón simétrico/repetido incluso con pocos productos.
        phase = rank * 2.4
        yfrac = FLOAT_AMPLITUDE * math.sin(phase)
        # La inclinación se deriva de cuánto "sube" cada uno (no de una fase
        # independiente): más alto = más inclinado, como si estuviera girando
        # al flotar. Evita además que dos senos con distinta frecuencia caigan
        # por coincidencia cerca de 0 al mismo tiempo con pocos productos.
        rot = FLOAT_TILT_MAX * (yfrac / FLOAT_AMPLITUDE)
        prepared.append({"item": it, "yfrac": yfrac, "rotation": rot})

    xs, x = [], 0.0
    for p in prepared:
        xs.append(x)
        x += p["item"].sw * (1 - overlap)

    max_h = max(p["item"].sh for p in prepared)
    margin = round(base_height * 0.20)

    ys, min_top = [], 0.0
    for p in prepared:
        it = p["item"]
        y = (max_h - it.sh) / 2 + p["yfrac"] * max_h
        ys.append(y)
        min_top = min(min_top, y)

    # Piso de referencia = la base más baja entre todos los productos.
    floor_y = max(ys[i] - min_top + p["item"].sh for i, p in enumerate(prepared))

    out = []
    max_right = max_bottom = 0.0
    for i, p in enumerate(prepared):
        it, rot = p["item"], p["rotation"]
        x0 = xs[i] + margin
        y0 = (ys[i] - min_top) + margin
        rw, rh = _rotated_bbox(it.sw, it.sh, rot) if rot else (it.sw, it.sh)
        drop = max(0.0, floor_y - ((ys[i] - min_top) + it.sh))
        out.append({
            "name": it.name, "filepath": it.filepath,
            "ow": it.w, "oh": it.h, "sw": it.sw, "sh": it.sh,
            "x": round(x0), "y": round(y0),
            "z": float(i),
            "rotation": round(rot, 2),
            "float_drop": round(min(1.0, drop / max_h), 3),
        })
        max_right = max(max_right, x0 + rw / 2 + it.sw / 2)
        max_bottom = max(max_bottom, y0 + rh / 2 + it.sh / 2)

    doc_w = min(math.ceil(max_right) + margin, 30000)
    doc_h = min(math.ceil(max_bottom) + margin, 30000)

    return {
        "canvas_width": doc_w, "canvas_height": doc_h,
        "rows_count": 1, "sort_strategy": "flotante", "items": out,
        "effects": {
            "shadow": True, "depth": True, "tilt": True,
            "shadow_opacity": 0.26,
            "shadow_blur": max(8, round(base_height * 0.04)),
            "shadow_margin": round(base_height * 0.10),
            "floating_shadow": True,
        },
    }


MIN_SEPARATION_FRAC = 0.42  # fracción del "radio" combinado que debe haber, como mínimo, entre centros


def _enforce_min_separation(placed, min_frac: float = MIN_SEPARATION_FRAC, iterations: int = 6):
    """Si dos productos quedan con las cajas demasiado superpuestas, los
    separa a lo largo de la línea que une sus centros (no solo en X): con
    6-7 productos es común que dos queden encimados en diagonal o a
    distinta altura, y revisar solo pares vecinos en X (versión anterior)
    no atrapaba esos casos — se veían tapados igual aunque sus cx ya
    estuvieran "separados" en la fracción 0-1 que mandó la IA.

    placed: [(item, cx, cy, rotation), ...] en píxeles del lienzo de
    referencia. Todos-contra-todos (n es como mucho ~15) y varias pasadas,
    porque separar un par puede volver a acercar otro que ya estaba bien."""
    placed = list(placed)
    n = len(placed)
    for _ in range(iterations):
        moved = False
        for i in range(n):
            for j in range(i + 1, n):
                it_a, cx_a, cy_a, rot_a = placed[i]
                it_b, cx_b, cy_b, rot_b = placed[j]
                # Radio "efectivo" de cada item — promedio de medio-ancho y
                # medio-alto (no la caja rotada exacta): alcanza para atrapar
                # superposición grosera sin complicar con geometría rotada.
                ra = (it_a.sw + it_a.sh) / 4
                rb = (it_b.sw + it_b.sh) / 4
                min_dist = min_frac * 2 * (ra + rb)
                dx, dy = cx_b - cx_a, cy_b - cy_a
                dist = math.hypot(dx, dy)
                if dist < min_dist:
                    moved = True
                    if dist < 1e-6:
                        dx, dy, dist = 1.0, 0.0, 1.0  # centros idénticos: empuja horizontal
                    push = (min_dist - dist) / 2
                    ux, uy = dx / dist, dy / dist
                    placed[i] = (it_a, cx_a - ux * push, cy_a - uy * push, rot_a)
                    placed[j] = (it_b, cx_b + ux * push, cy_b + uy * push, rot_b)
        if not moved:
            break
    return placed


def compute_layout_ai_freeform(
    images: List[dict],
    free_layout: dict,
    base_height: int = 760,
    max_vertical_boost: float = 1.20,
    **_ignored,
) -> dict:
    """'IA · Libre': la composición (posición + rotación de cada producto) la
    propone directamente la IA de visión (ver ai_proposal._build_prompt),
    no una fórmula geométrica nuestra. Este layout SOLO traduce esas
    coordenadas normalizadas (0-1, sobre un lienzo cuadrado de referencia) a
    píxeles y ajusta el canvas al contenido — la IA nunca toca las imágenes
    en sí, siguen siendo las fotos reales, así que no hay riesgo de que se
    altere ninguna etiqueta.

    free_layout: {nombre: {"cx":0-1, "cy":0-1, "rotation":-35_a_35}, ...}
    (ver ai_proposal._normalize_free_layout — siempre trae los 3 campos,
    con 0.5/0.5/0 de default si la IA no propuso algo válido para ese item).
    """
    n = len(images)
    if n == 0:
        raise ValueError("No se proporcionaron imágenes")
    free_layout = free_layout or {}

    items = _scale_items(images, base_height, max_vertical_boost)
    # Lienzo de referencia derivado del tamaño REAL ya escalado de los
    # productos (no de base_height a secas) — si no, con pocos productos
    # altos el cuadrado de referencia queda enorme comparado con ellos y las
    # posiciones 0-1 de la IA terminan dispersas, con huecos vacíos entre
    # medio en vez de la superposición/profundidad que pide el prompt.
    #
    # spread_factor crece con la cantidad de productos: con 2-3 el lienzo
    # queda chico a propósito (fuerza el solapamiento/profundidad que pide
    # el prompt), pero ese mismo 0.6 fijo con 6-7 productos no alcanza —
    # aunque la IA separe bien sus cx/cy en la fracción 0-1, el lienzo de
    # referencia es tan chico que esa separación se traduce en apenas unos
    # px reales y los productos quedan pisándose de nuevo. Con más
    # productos hace falta más lienzo para que el mismo 0-1 signifique más
    # espacio real entre ellos.
    n_items = len(items)
    spread_factor = min(1.0, 0.6 + 0.06 * max(0, n_items - 3))
    total_w = sum(it.sw for it in items)
    max_h = max(it.sh for it in items)
    ref_side = max(max_h, total_w * spread_factor) * 1.15

    placed = []
    for it in items:
        pos = free_layout.get(it.name, {"cx": 0.5, "cy": 0.5, "rotation": 0.0})
        cx = float(pos.get("cx", 0.5)) * ref_side
        cy = float(pos.get("cy", 0.5)) * ref_side
        rot = float(pos.get("rotation", 0.0))
        placed.append((it, cx, cy, rot))

    # Red de seguridad: la IA propone coordenadas "a ciegas" (sin ver un
    # render real), y a veces dos productos quedan con los centros demasiado
    # juntos — uno tapando casi por completo al otro, en cualquier
    # dirección (no solo lado a lado). Se separan lo mínimo necesario;
    # rotación queda igual a lo que propuso la IA.
    placed = _enforce_min_separation(placed)

    min_left = min_top = 0.0
    max_right = max_bottom = 0.0
    for it, cx, cy, rot in placed:
        rw, rh = _rotated_bbox(it.sw, it.sh, rot) if rot else (it.sw, it.sh)
        min_left = min(min_left, cx - rw / 2)
        min_top = min(min_top, cy - rh / 2)
        max_right = max(max_right, cx + rw / 2)
        max_bottom = max(max_bottom, cy + rh / 2)

    margin = round(base_height * 0.12)
    # Sin dato de profundidad explícito de la IA: el más grande (más
    # prominente) se pinta al frente, criterio consistente con el resto del motor.
    order_by_size = sorted(range(len(placed)), key=lambda i: placed[i][0].sh)

    out = []
    for rank, idx in enumerate(order_by_size):
        it, cx, cy, rot = placed[idx]
        x0 = (cx - it.sw / 2) - min_left + margin
        y0 = (cy - it.sh / 2) - min_top + margin
        out.append({
            "name": it.name, "filepath": it.filepath,
            "ow": it.w, "oh": it.h, "sw": it.sw, "sh": it.sh,
            "x": round(x0), "y": round(y0),
            "z": float(rank),
            "rotation": round(rot, 2),
        })

    doc_w = min(math.ceil(max_right - min_left) + margin * 2, 30000)
    doc_h = min(math.ceil(max_bottom - min_top) + margin * 2, 30000)

    return {
        "canvas_width": doc_w, "canvas_height": doc_h,
        "rows_count": 1, "sort_strategy": "ai_libre", "items": out,
        "effects": {
            "shadow": True, "depth": True, "tilt": True,
            "shadow_opacity": 0.28,
            "shadow_blur": max(8, round(base_height * 0.04)),
            "shadow_margin": round(base_height * 0.10),
        },
    }


STANDALONE_STRATEGIES = {
    'cascada':  compute_layout_cascada,
    'radial':   compute_layout_radial,
    'flotante': compute_layout_flotante,
}


def compute_layout_any(images: List[dict], sort_strategy: str = 'auto', **kwargs) -> dict:
    """Punto de entrada único: enruta a compute_layout() (motor de filas) o a
    un layout standalone (cascada/radial) según la estrategia pedida."""
    fn = STANDALONE_STRATEGIES.get(sort_strategy)
    if fn:
        return fn(images, base_height=kwargs.get('base_height', 760),
                   max_vertical_boost=kwargs.get('max_vertical_boost', 1.20))
    return compute_layout(images, sort_strategy=sort_strategy, **kwargs)
