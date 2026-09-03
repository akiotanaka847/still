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


def _center_named_hero(rows: List[_Row], hero_name: str) -> List[_Row]:
    """Centra el héroe indicado sin descartar el orden de dirección de arte.

    Los acompañantes se ubican por prioridad alrededor del centro: primero a
    la derecha, luego a la izquierda y después hacia los extremos.
    """
    if not hero_name:
        return rows
    for row in rows:
        hero = next((item for item in row.items if item.name == hero_name), None)
        if hero is None or len(row.items) < 2:
            continue
        supports = [item for item in row.items if item is not hero]
        result: list = [None] * len(row.items)
        mid = (len(result) - 1) // 2
        result[mid] = hero
        slots = []
        for distance in range(1, len(result) + 1):
            right = mid + distance
            left = mid - distance
            if right < len(result):
                slots.append(right)
            if left >= 0:
                slots.append(left)
        for slot, item in zip(slots, supports):
            result[slot] = item
        row.items = [item for item in result if item is not None]
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
    'ai_profundidad_grad': list,
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
    'ai_depth':         {'overlap': 0.14, 'hero_scale': 1.16, 'falloff': 0.97},
    'ai_profundidad_grad': {'overlap': 0.18, 'hero_scale': 1.14, 'falloff': 0.90},
    'impacto':          {'overlap': 0.22, 'hero_scale': 1.50, 'falloff': 0.88, 'tilt': True, 'tilt_mode': 'fan'},
    'ai_impacto':       {'overlap': 0.18, 'hero_scale': 1.24, 'falloff': 0.95, 'tilt': True, 'tilt_mode': 'fan'},
    'fila_recta':       {'overlap': 0.16, 'hero_scale': 1.00, 'falloff': 1.00, 'center_hero': False},
    'ai_fila_recta':    {'overlap': 0.16, 'hero_scale': 1.00, 'falloff': 1.00, 'center_hero': False},
    'cruce':            {'overlap': 0.32, 'hero_scale': 1.15, 'falloff': 1.00, 'tilt': True, 'tilt_mode': 'converge'},
    'ai_cruce':         {'overlap': 0.24, 'hero_scale': 1.10, 'falloff': 0.98, 'tilt': True, 'tilt_mode': 'converge'},
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


# Tope del lado mayor del canvas final, en píxeles (peso/tiempo de render).
# renderer.py lo APLICA reduciendo el layout; acá se necesita para saber si el
# tope va a apretar, que es lo único que hace que el reparto de filas importe.
MAX_CANVAS_DIM = 4000


def _medir_reparto(items: List[_Item], rows_count: int, item_gap: int) -> tuple:
    """Ancho y alto del lienzo para un reparto dado, con el empaquetado REAL.

    Repite el mismo reparto que hace compute_layout (cada producto va a la fila
    más angosta) en vez de estimarlo. La estimación por "productos por fila x
    razón media" erraba mucho: las filas se balancean por ANCHO, y con
    productos que van de 0.23 a 2.80 de razón, dos filas con la misma cantidad
    pueden medir muy distinto. Ese error hacía elegir repartos peores que el
    anterior en sets de 16 y 25 productos.
    """
    anchos = [0.0] * rows_count
    altos = [0.0] * rows_count
    for item in items:
        i = min(range(rows_count), key=lambda k: anchos[k])
        anchos[i] += item.sw + item_gap
        if item.sh > altos[i]:
            altos[i] = item.sh
    if any(a == 0.0 for a in altos):        # fila vacía: reparto inválido
        return None
    return max(anchos), sum(altos)


def _elegir_filas(items: List[_Item], item_gap: int, target_aspect: float = 0.0) -> int:
    """Cuántas filas conviene para estos productos.

    Hasta 15 manda la escalera original: una hilera (o dos) en sets chicos es
    la composición clásica de bodegón, decisión estética que optimizar por
    tamaño rompía —con 6 productos elegía 2 filas porque el producto sale 84%
    más grande, que es cierto y es peor pieza.

    De 16 en adelante, que es donde el reparto fijo de 3 filas fallaba (60
    productos daban una tira de 4.2:1), se elige midiendo: renderer.py topa el
    LADO MAYOR de la salida, así que gana el reparto cuyo lado mayor sea más
    chico — el producto termina más grande. El piso de aspecto evita que la
    pieza salga marcadamente más alta que ancha.
    """
    n = len(items)

    # Una sola regla para todo n: SI EL REPARTO DE SIEMPRE CABE bajo el tope,
    # se respeta. Es lo que preserva la estética deliberada de los sets chicos
    # —una hilera de productos es la composición clásica de bodegón, y
    # optimizar por tamaño elegía 2 filas con 6 productos: 84% más grandes y
    # peor pieza— y a la vez evita mover casos que no estaban rotos: con 30
    # productos altos, 3 filas dan 3929x2736, entra en 4000 sin reducir, y
    # recalcular movía el alto del producto de 916 a 895 px por otra vía.
    #
    # Cuando NO cabe es cuando el reparto importa, y ahí sí se calcula. Eso
    # cubre los dos casos que estaban mal, no solo el grande: 60 productos
    # daban una tira de 4.2:1, pero 6 productos ANCHOS daban una de 9.97:1 y
    # el producto salía a 375 px cuando podía salir al doble.
    fijo = 1 if n <= 6 else (2 if n <= 15 else 3)
    base = _medir_reparto(items, fijo, item_gap)
    if base and not target_aspect and max(base) <= MAX_CANVAS_DIM:
        return fijo

    # 0.85 se eligió midiendo, no a ojo: con 0.7 y 0.8 el set de 25 productos
    # elegía un reparto 4% PEOR que el anterior y dejaba piezas de aspecto
    # 0.75 (notablemente más altas que anchas). Con 0.85 y 0.9 no hay ninguna
    # regresión en el rango 16-60 y la ganancia media es la misma (+26%), así
    # que se toma el valor menos al borde de los dos.
    ASPECTO_MINIMO = 0.85
    mejor, mejor_puntaje = 3, -1e9
    for r in range(1, n + 1):
        medida = _medir_reparto(items, r, item_gap)
        if medida is None:
            break                            # más filas solo dejan más vacías
        w, h = medida
        aspecto = w / h if h else 0.0
        if r > 1 and aspecto < ASPECTO_MINIMO:
            continue
        puntaje = -abs(aspecto - target_aspect) if target_aspect else 1.0 / max(w, h)
        if puntaje > mejor_puntaje:
            mejor_puntaje, mejor = puntaje, r
    return mejor


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
    hero_name: str = None,
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

    # ─── Cuántas filas ───────────────────────────────────────────────────────
    # Antes esto era una escalera fija (1 fila hasta 6, 2 hasta 15, 3 en
    # adelante) y aspect_w/aspect_h se asignaban pero NO SE USABAN en ningún
    # lado: eran parámetros muertos. Con el canvas exacto al contenido, 3 filas
    # de N crecen SOLO en horizontal, así que a partir de ~30 productos la
    # pieza se estiraba sin techo: 60 productos daban 12510x2975 (4.2:1).
    #
    # El criterio ahora es el que de verdad importa: renderer.py topa el LADO
    # MAYOR de la salida en MAX_CANVAS_DIM, así que el producto sale más grande
    # cuando el lienzo se acerca al CUADRADO — es el reparto que menos
    # desperdicia en el lado que manda. Se elige el número de filas que
    # minimiza ese lado mayor (medido en anchos de producto).
    #
    # El piso de aspecto 1.0 es lo que evita que la optimización se pase de
    # lista: sin él, 14 productos irían a 3 filas (aspecto 0.83, producto más
    # grande) y la pieza saldría más alta que ancha, que no es un bodegón.
    # Con el piso, todo n <= 30 conserva el reparto que ya tenía.
    if aspect_w and aspect_h:
        target_aspect = aspect_w / aspect_h
    else:
        target_aspect = 0.0
        aspect_w, aspect_h = 0, 3

    # Escalar items
    items: List[_Item] = _scale_items(images, base_height, max_vertical_boost)

    # Aplicar estrategia de ordenamiento
    sort_fn = SORT_STRATEGIES.get(sort_strategy, _sort_auto)
    items = sort_fn(items)

    if rows_count == 0:
        rows_count = _elegir_filas(items, item_gap, target_aspect)

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
    if depth and hero_name:
        rows = _center_named_hero(rows, hero_name)
    elif sort_strategy in ('centrado', 'auto', 'area') or (depth and center_hero):
        rows = _center_in_rows(rows)

    # Profundidad: escalar según la posición ya centrada
    #   héroe (el más alto, queda al centro) → hero_scale
    #   cada paso de índice hacia los lados → ×falloff
    row_hero = {}
    if depth:
        for ri, row in enumerate(rows):
            if not row.items:
                continue
            hero_idx = next(
                (i for i, item in enumerate(row.items) if item.name == hero_name),
                max(range(len(row.items)), key=lambda i: row.items[i].sh),
            )
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
                # Todos los productos de una fila comparten el mismo borde
                # inferior (ver "alineados a la base" arriba) — al rotar,
                # el renderer debe conservar ESE borde, no el centro, para
                # que sigan quedando al ras entre sí sin importar que cada
                # uno tenga un ángulo/tamaño distinto (ver renderer._compose).
                "anchor": "bottom",
            })
        cy += row.height + shadow_margin + item_gap

    # Alto: conserva el margen de sombra de la última fila, descarta el gap final
    doc_w = math.ceil(widest)
    doc_h = math.ceil(cy - item_gap)

    # La inclinación hace que las esquinas del producto sobresalgan de su caja
    # original. Antes se armaba un solo margen "peor caso" (el mayor
    # crecimiento de un producto cualquiera) y se aplicaba IGUAL a los
    # cuatro lados de TODOS los productos — si el que más rotaba quedaba
    # cerca del centro, el canvas igual reservaba ese margen completo en
    # los bordes, dejando franjas de transparencia vacías donde no hacía
    # falta. Ahora se calcula el bounding box REAL combinado (cada
    # producto con su propia rotación, respetando el mismo anclaje que usa
    # el renderer: por el borde inferior si anchor="bottom", si no por el
    # centro) y el canvas se ajusta exacto a eso — mismo criterio que ya
    # usan Cascada/Radial/Flotante/IA Libre más abajo en este archivo.
    if tilt:
        min_left = min_top = 0.0
        max_right = max_bottom = 0.0
        for it in out:
            rot = it.get("rotation", 0.0)
            cx = it["x"] + it["sw"] / 2
            if rot:
                rw, rh = _rotated_bbox(it["sw"], it["sh"], rot)
            else:
                rw, rh = it["sw"], it["sh"]
            left, right = cx - rw / 2, cx + rw / 2
            if it.get("anchor") == "bottom":
                bottom = it["y"] + it["sh"]
                top = bottom - rh
            else:
                cy_it = it["y"] + it["sh"] / 2
                top, bottom = cy_it - rh / 2, cy_it + rh / 2
            min_left = min(min_left, left)
            max_right = max(max_right, right)
            min_top = min(min_top, top)
            max_bottom = max(max_bottom, bottom)

        pad = 4
        # La sombra de contacto se dibuja DEBAJO de cada producto (nunca
        # arriba ni a los costados) — sin este espacio extra en la base,
        # el difuminado queda cortado contra el borde del canvas.
        bottom_pad = pad + shadow_margin
        shift_x = pad - min_left
        shift_y = pad - min_top
        for it in out:
            it["x"] = round(it["x"] + shift_x)
            it["y"] = round(it["y"] + shift_y)
        doc_w = math.ceil(max_right - min_left) + pad * 2
        doc_h = math.ceil(max_bottom - min_top) + pad + bottom_pad

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
    hero_name: str = None,
    preserve_order: bool = False,
    **_ignored,
) -> dict:
    """Cascada diagonal: todos los productos inclinados en la MISMA dirección
    (no en abanico), creciendo en tamaño hacia una esquina, solapados —
    transmite flujo/movimiento en vez de simetría. Pensado para una línea de
    producto (mismo SKU en variantes) más que para un héroe puntual."""
    n = len(images)
    if n == 0:
        raise ValueError("No se proporcionaron imágenes")

    if preserve_order:
        growth = min(growth, 1.12)
        overlap = max(overlap, 0.22)
        tilt_deg = max(-12.0, min(12.0, tilt_deg))
        step_up = min(step_up, 0.22)

    base_items = _scale_items(images, base_height, max_vertical_boost)
    if preserve_order:
        order = list(range(n))
        if hero_name:
            order = [i for i in order if base_items[i].name != hero_name] + [
                i for i in order if base_items[i].name == hero_name
            ]
    else:
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
    hero_name: str = None,
    preserve_order: bool = False,
    **_ignored,
) -> dict:
    """Radial/Explosión: alturas y ángulos variados por posición — ninguno
    sobre una única línea de base, como si irradiaran desde un punto central.
    Pensado para grupos de 3+ productos distintos entre sí."""
    n = len(images)
    if n == 0:
        raise ValueError("No se proporcionaron imágenes")

    if preserve_order:
        overlap = max(overlap, 0.10)
        max_tilt = min(max_tilt, 16.0)
        height_jitter = min(height_jitter, 0.08)

    items = _scale_items(images, base_height, max_vertical_boost)
    order = list(range(n)) if preserve_order else sorted(range(n), key=lambda i: items[i].sh, reverse=True)
    if hero_name and preserve_order:
        hero_idx = next((i for i in order if items[i].name == hero_name), None)
        if hero_idx is not None:
            supports = [i for i in order if i != hero_idx]
            mid_slot = (n - 1) // 2
            arranged = [None] * n
            arranged[mid_slot] = hero_idx
            slots = []
            for distance in range(1, n + 1):
                right = mid_slot + distance
                left = mid_slot - distance
                if right < n:
                    slots.append(right)
                if left >= 0:
                    slots.append(left)
            for slot, item_idx in zip(slots, supports):
                arranged[slot] = item_idx
            order = [i for i in arranged if i is not None]
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
    hero_name: str = None,
    preserve_order: bool = False,
    **_ignored,
) -> dict:
    """Flotante: cada producto a su propia altura (sin línea de base
    compartida) e inclinación leve, como si flotaran a distintas distancias
    del piso. La sombra de contacto se achica/desvanece según qué tan
    'lejos' flota cada uno (ver float_drop → renderer._draw_contact_shadow)."""
    n = len(images)
    if n == 0:
        raise ValueError("No se proporcionaron imágenes")

    if preserve_order:
        overlap = max(overlap, 0.06)

    items = _scale_items(images, base_height, max_vertical_boost)
    order = list(range(n)) if preserve_order else sorted(range(n), key=lambda i: items[i].sh, reverse=True)
    if hero_name and preserve_order:
        hero_idx = next((i for i in order if items[i].name == hero_name), None)
        if hero_idx is not None:
            supports = [i for i in order if i != hero_idx]
            mid_slot = (n - 1) // 2
            arranged = [None] * n
            arranged[mid_slot] = hero_idx
            slots = []
            for distance in range(1, n + 1):
                right = mid_slot + distance
                left = mid_slot - distance
                if right < n:
                    slots.append(right)
                if left >= 0:
                    slots.append(left)
            for slot, item_idx in zip(slots, supports):
                arranged[slot] = item_idx
            order = [i for i in arranged if i is not None]

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


ALLOWED_OVERLAP_FRAC = 0.15  # cuánto pueden "meterse" las cajas una en la otra
                              # a lo sumo, en la dirección que une sus centros
                              # (0 = bordes apenas se tocan; 1 = se permiten
                              # cubrir del todo).


def _enforce_min_separation(placed, allowed_overlap: float = ALLOWED_OVERLAP_FRAC, iterations: int = 8):
    """Si dos productos quedan con las cajas demasiado superpuestas, los
    separa a lo largo de la línea que une sus centros (no solo en X): con
    6-7 productos es común que dos queden encimados en diagonal o a
    distinta altura, y revisar solo pares vecinos en X (versión anterior)
    no atrapaba esos casos — se veían tapados igual aunque sus cx ya
    estuvieran "separados" en la fracción 0-1 que mandó la IA.

    La distancia mínima requerida entre dos centros se calcula proyectando
    el semiancho/semialto de la caja YA ROTADA de cada item sobre la
    dirección que une ambos centros (en vez de promediar ancho y alto en un
    solo "radio" sin dirección, como antes) — con productos de tamaño/forma
    parecida daba resultados similares, pero con un producto mucho más
    grande que otro (p. ej. una caja ancha junto a un frasco chico) esa
    versión permitía que el chico quedara casi enteramente tapado sin
    detectarlo como superposición grave, porque el "radio promedio" del
    grande no reflejaba su verdadero ancho en esa dirección.

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
                rw_a, rh_a = _rotated_bbox(it_a.sw, it_a.sh, rot_a) if rot_a else (it_a.sw, it_a.sh)
                rw_b, rh_b = _rotated_bbox(it_b.sw, it_b.sh, rot_b) if rot_b else (it_b.sw, it_b.sh)
                dx, dy = cx_b - cx_a, cy_b - cy_a
                dist = math.hypot(dx, dy)
                if dist < 1e-6:
                    dx, dy, dist = 1.0, 0.0, 1.0  # centros idénticos: empuja horizontal
                ux, uy = dx / dist, dy / dist
                # Semiancho/semialto de cada caja proyectado sobre (ux,uy):
                # cuánto ocupa CADA item, específicamente en la dirección
                # hacia el otro — no un promedio ciego a la dirección.
                extent_a = (rw_a / 2) * abs(ux) + (rh_a / 2) * abs(uy)
                extent_b = (rw_b / 2) * abs(ux) + (rh_b / 2) * abs(uy)
                min_dist = (extent_a + extent_b) * (1 - allowed_overlap)
                if dist < min_dist:
                    moved = True
                    push = (min_dist - dist) / 2
                    placed[i] = (it_a, cx_a - ux * push, cy_a - uy * push, rot_a)
                    placed[j] = (it_b, cx_b + ux * push, cy_b + uy * push, rot_b)
        if not moved:
            break
    return placed


def _compact_layout(placed, shrink_step: float = 0.9, max_iterations: int = 120):
    """Achica la composición hacia su centroide en pasos chicos, volviendo a
    exigir separación mínima (_enforce_min_separation) después de cada paso,
    hasta que la dispersión total deja de reducirse (equilibrio).

    Por qué hace falta: el lienzo de referencia (ref_side, más abajo) crece
    con la cantidad de productos para evitar que se amontonen, pero la IA
    propone sus coordenadas 0-1 sin saber qué tan grande va a terminar
    siendo ese lienzo — con grupos grandes (7+), sus propias coordenadas ya
    bastante separadas más un lienzo grande se combinan y el resultado
    queda con espacio muerto enorme entre productos, sin ninguna cohesión
    visual (reportado por el usuario con un caso real de 9 productos).
    Compactar DESPUÉS de ubicar y separar, en vez de intentar adivinar de
    antemano cuánto lienzo hace falta, es más simple y siempre exacto —
    mismo principio que ya se usó para el margen del canvas (ver "Canvas
    transparente exacto al contenido" en la memoria del proyecto).

    Por qué es por pasos chicos y no un solo salto grande: si un solo par ya
    está en su distancia mínima, un achicamiento grande de una sola vez hace
    que ESE par frene TODO el achicamiento, aunque el resto del grupo tenga
    espacio de sobra para seguir juntándose. Yendo de a pasos chicos y
    re-separando en cada uno, ese par se queda fijo en su mínimo (la
    separación lo empuja de vuelta ahí) mientras los demás siguen
    acercándose — el criterio de parada es que la dispersión total del
    grupo deje de bajar entre una vuelta y la siguiente, no que un par
    puntual haya llegado a su límite.

    shrink_step=0.9 y max_iterations=120 son el resultado de medir con hasta
    60 productos (el máximo de la app): con un paso más chico (0.98) el
    resultado final es prácticamente idéntico pero tarda ~4x más (~1.8s vs
    ~0.5s con 60 productos) — el paso solo afecta CUÁNTAS vueltas hacen
    falta para llegar al mismo lugar, no A DÓNDE llega.

    Aplica desde 2 productos (con pocos, si ya están ajustados, converge en
    la primera vuelta y no se mueve nada) hasta el máximo de la app (60) —
    sin casos especiales por cantidad."""
    if len(placed) < 2:
        return placed

    def _spread(pts):
        # Suma de distancias al centroide — mide qué tan agrupado está el
        # conjunto EN GENERAL. El bounding box (max-min) NO sirve acá: queda
        # dominado por el par más alejado, y si ESE par ya está en su
        # distancia mínima (no puede acercarse más), el bounding box deja de
        # bajar aunque el resto del grupo todavía tenga lugar para juntarse.
        cx0 = sum(cx for _, cx, cy, _ in pts) / len(pts)
        cy0 = sum(cy for _, cx, cy, _ in pts) / len(pts)
        return sum(math.hypot(cx - cx0, cy - cy0) for _, cx, cy, _ in pts)

    current = placed
    current_spread = _spread(current)
    for _ in range(max_iterations):
        cx0 = sum(cx for _, cx, cy, _ in current) / len(current)
        cy0 = sum(cy for _, cx, cy, _ in current) / len(current)
        shrunk = [
            (it, cx0 + (cx - cx0) * shrink_step, cy0 + (cy - cy0) * shrink_step, rot)
            for it, cx, cy, rot in current
        ]
        enforced = _enforce_min_separation(shrunk)
        new_spread = _spread(enforced)
        # Achicar + re-separar puede a veces AUMENTAR la dispersión (una
        # corrección local empuja un producto más lejos del centro de lo que
        # estaba) — solo se acepta la vuelta si de verdad agrupó más al
        # conjunto; si no, se corta ahí y se devuelve el mejor estado ya
        # alcanzado.
        if new_spread >= current_spread - 0.5:
            break
        current = enforced
        current_spread = new_spread
    return current


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

    # Elimina el espacio muerto que puede quedar entre productos, sobre todo
    # con grupos grandes — ver _compact_layout.
    placed = _compact_layout(placed)

    min_left = min_top = 0.0
    max_right = max_bottom = 0.0
    for it, cx, cy, rot in placed:
        rw, rh = _rotated_bbox(it.sw, it.sh, rot) if rot else (it.sw, it.sh)
        min_left = min(min_left, cx - rw / 2)
        min_top = min(min_top, cy - rh / 2)
        max_right = max(max_right, cx + rw / 2)
        max_bottom = max(max_bottom, cy + rh / 2)

    margin = round(base_height * 0.12)
    # Sin dato de profundidad explícito de la IA: combinamos posición vertical
    # y tamaño. En fotografía de producto, lo que está más abajo en el lienzo
    # suele leerse más al frente; sumar un poco de altura conserva al héroe
    # grande con prioridad sin dejar que tape arbitrariamente a secundarios
    # que la IA colocó en primer plano.
    order_by_depth = sorted(range(len(placed)), key=lambda i: placed[i][2] + placed[i][0].sh * 0.18)

    out = []
    for rank, idx in enumerate(order_by_depth):
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
    'ai_cascada': compute_layout_cascada,
    'radial':   compute_layout_radial,
    'ai_radial': compute_layout_radial,
    'flotante': compute_layout_flotante,
    'ai_flotante': compute_layout_flotante,
}


def compute_layout_any(images: List[dict], sort_strategy: str = 'auto', **kwargs) -> dict:
    """Punto de entrada único: enruta a compute_layout() (motor de filas) o a
    un layout standalone (cascada/radial) según la estrategia pedida."""
    fn = STANDALONE_STRATEGIES.get(sort_strategy)
    if fn:
        standalone_kwargs = dict(kwargs)
        standalone_kwargs.setdefault('base_height', 760)
        standalone_kwargs.setdefault('max_vertical_boost', 1.20)
        if sort_strategy.startswith('ai_'):
            standalone_kwargs['preserve_order'] = True
        return fn(images, **standalone_kwargs)
    return compute_layout(images, sort_strategy=sort_strategy, **kwargs)
