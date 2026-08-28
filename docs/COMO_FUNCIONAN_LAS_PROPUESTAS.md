# Cómo funcionan las propuestas: con IA vs. sin IA

Referencia técnica con el código real de cada propuesta visual del Generador de
Bodegón. Para la vista funcional/de producto ver [`GENERADOR_BODEGON.md`](GENERADOR_BODEGON.md);
este documento es el complemento con el código fuente.

## Principio central

Todas las propuestas —con o sin IA— terminan en el mismo lugar: una lista de
posiciones `{x, y, rotation, scale}` por producto, que Pillow usa para pegar las
**fotos reales** sobre un canvas. Ninguna propuesta genera ni retoca píxeles de
producto.

- **Sin IA**: una fórmula fija (Python, [`layout.py`](../layout.py)) calcula esas
  posiciones a partir del tamaño de las fotos. Determinístico: mismo input →
  mismo resultado, siempre, sin llamar a ningún servicio externo.
- **Con IA**: un modelo de visión (Groq/Gemini/Claude/OpenAI, [`ai_proposal.py`](../ai_proposal.py))
  mira las fotos y devuelve **números** — orden, tamaño relativo, y en el caso de
  "IA · Libre", posición/ángulo por producto. Esos números se validan y se le
  pasan al mismo motor de Pillow. La IA nunca ve ni genera una imagen de salida.

Esto último no es una decisión de diseño arbitraria: se probó pedirle a un
modelo de generación de imagen (`gpt-image-1`) que compusiera directamente el
bodegón, y distorsionó texto de las etiquetas ("Red Bull" → "RodBuil",
"TROPICAL" → "FROPICAL"). Por eso la arquitectura fuerza la separación: **la IA
propone números, el código determinístico dibuja la foto real**.

---

## Parte 1 — Propuestas SIN IA (motor geométrico)

15 de las 18 propuestas sin IA son la **misma función**, [`compute_layout()`](../layout.py#L178),
configurada distinto por dos diccionarios: `SORT_STRATEGIES` (cómo se ordenan
los productos) y `DEPTH_PARAMS` (cuánto se solapan, cuánto crece el héroe,
cuánto se inclinan). Las otras 3 (Cascada, Radial, Flotante) no encajan en el
modelo de filas y son funciones aparte.

Se ejecuta dos veces por cada render: en **Python** (`layout.py`, la verdad
final que termina en el PNG) y espejada en **JavaScript**
(`computeLayoutJS`, [`index.html:1136`](../static/index.html#L1136)) para que la
vista previa en el navegador sea instantánea, sin ida y vuelta al servidor.

### 1.1 El motor de filas: `compute_layout()`

```python
# layout.py

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

DEPTH_PARAMS = {
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
    'inclinado_derecha':    {'overlap': 0.16, 'hero_scale': 1.00, 'falloff': 1.00, 'center_hero': False,
                              'tilt': True, 'tilt_mode': 'uniform', 'tilt_angle': 30.0},
    'ai_inclinado_derecha': {'overlap': 0.16, 'hero_scale': 1.00, 'falloff': 1.00, 'center_hero': False,
                              'tilt': True, 'tilt_mode': 'uniform', 'tilt_angle': 30.0},
    'inclinado_izquierda':    {'overlap': 0.16, 'hero_scale': 1.00, 'falloff': 1.00, 'center_hero': False,
                                'tilt': True, 'tilt_mode': 'uniform', 'tilt_angle': -30.0},
    'ai_inclinado_izquierda': {'overlap': 0.16, 'hero_scale': 1.00, 'falloff': 1.00, 'center_hero': False,
                                'tilt': True, 'tilt_mode': 'uniform', 'tilt_angle': -30.0},
}


def compute_layout(
    images: List[dict],
    sort_strategy: str = 'auto',
    rows_count: int = 0,
    base_height: int = 760,
    item_gap: int = 40,
    internal_padding: int = 0,
    max_vertical_boost: float = 1.20,
    aspect_w: int = 0,
    aspect_h: int = 0,
    tilt_angle: float = None,  # solo tilt_mode='uniform': magnitud elegida por el
                                # usuario (slider); el signo lo define la estrategia.
) -> dict:
    n = len(images)
    if n == 0:
        raise ValueError("No se proporcionaron imágenes")

    depth = sort_strategy in DEPTH_PARAMS
    strong_shadow = depth or sort_strategy in ('sombra', 'ai_shadow')
    shadow = True
    dp = DEPTH_PARAMS.get(sort_strategy, {})
    overlap = dp.get('overlap', 0.0)
    hero_scale = dp.get('hero_scale', 1.0)
    depth_falloff = dp.get('falloff', 1.0)
    tilt = bool(dp.get('tilt', False))
    tilt_mode = dp.get('tilt_mode', 'fan')
    shadow_opacity = 0.30 if strong_shadow else 0.16
    shadow_blur = max(8, round(base_height * (0.045 if strong_shadow else 0.034)))
    shadow_margin = round(base_height * (0.12 if strong_shadow else 0.07))

    if depth:
        rows_count = 1  # una sola fila para que solapamiento/héroe tengan sentido

    if sort_strategy == 'uniforme':
        max_vertical_boost = 1.0
    elif sort_strategy == 'dinamico':
        max_vertical_boost = 1.6

    if rows_count == 0 or aspect_w == 0:
        if n <= 6:
            rows_count = rows_count or 1; aspect_w = aspect_w or 5;  aspect_h = aspect_h or 3
        elif n <= 15:
            rows_count = rows_count or 2; aspect_w = aspect_w or 8;  aspect_h = aspect_h or 3
        else:
            rows_count = rows_count or 3; aspect_w = aspect_w or 10; aspect_h = aspect_h or 3
    aspect_h = aspect_h or 3

    items: List[_Item] = _scale_items(images, base_height, max_vertical_boost)

    sort_fn = SORT_STRATEGIES.get(sort_strategy, _sort_auto)
    items = sort_fn(items)   # ← acá se decide el ORDEN (fijo o el que trajo la IA)

    # Distribuir en filas (balance por ancho aproximado)
    rows: List[_Row] = [_Row() for _ in range(rows_count)]
    for item in items:
        row = min(rows, key=lambda r: r.width)
        row.items.append(item)
        row.width += item.sw + item_gap
        if item.sh > row.height:
            row.height = item.sh

    # El más alto al centro de la fila (silueta piramidal), salvo center_hero=False
    center_hero = dp.get('center_hero', True)
    if sort_strategy in ('centrado', 'auto', 'area') or (depth and center_hero):
        rows = _center_in_rows(rows)

    # Profundidad: héroe → hero_scale, cada paso hacia los lados → ×falloff
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

    for row in rows:
        if not row.items:
            row.width = 0.0; row.height = 0.0
            continue
        row.height = max(it.sh for it in row.items)
        if overlap > 0:
            total = sum(it.sw for it in row.items)
            total -= overlap * sum(it.sw for it in row.items[:-1])
            row.width = total
        else:
            row.width = sum(it.sw for it in row.items) + item_gap * (len(row.items) - 1)

    widest = max(r.width for r in rows)
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
            item.y = cy + (row.height - item.sh)
            z = (-abs(idx - hero_idx)) if depth else float(idx)
            rotation = 0.0
            if tilt:
                if tilt_mode == 'uniform':
                    default_angle = dp.get('tilt_angle', 30.0)
                    if tilt_angle is not None:
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
                "name": item.name, "filepath": item.filepath,
                "ow": item.w, "oh": item.h, "sw": item.sw, "sh": item.sh,
                "x": round(item.x), "y": round(item.y),
                "z": round(z, 3), "rotation": round(rotation, 2),
            })
        cy += row.height + shadow_margin + item_gap

    # (recorte de márgenes por rotación y armado del dict de salida — ver
    # layout.py:349-390 para el detalle completo)
    ...
```

Código completo, sin recortes: [`layout.py:178-390`](../layout.py#L178).

**Cómo leer las tablas de arriba** — tres perillas hacen toda la diferencia visual:

| Perilla | Qué controla | Ejemplo |
|---|---|---|
| `overlap` | Cuánto se solapan los productos en la fila | `0.32` en Cruce = mucho solape; `0.16` en Profundidad = solape sutil |
| `hero_scale` / `falloff` | Cuánto crece el héroe y qué tan rápido decrece el tamaño hacia los lados | `1.50`/`0.88` en Impacto = héroe muy dominante, caída marcada |
| `tilt_mode` | Cómo se calcula el ángulo de cada producto | `'fan'` = abanico hacia afuera (Impacto) · `'converge'` = hacia el centro (Cruce) · `'uniform'` = mismo ángulo fijo para todos (Inclinado, con el ángulo del slider) |

### 1.2 Los 3 layouts standalone (no usan `compute_layout`)

No encajan en el modelo de "filas balanceadas", así que son funciones propias.

**Cascada** — misma inclinación para todos (una sola dirección, no abanico),
creciendo en tamaño hacia una esquina, solapados:

```python
# layout.py:395-467
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
    n = len(images)
    base_items = _scale_items(images, base_height, max_vertical_boost)
    order = sorted(range(n), key=lambda i: base_items[i].sh)  # menor a mayor

    prepared = []
    g = 1.0
    for i in order:
        it = base_items[i]
        sh = round(it.sh * g)
        sw = round(sh * it.w / it.h)
        prepared.append({"item": it, "sw": sw, "sh": sh})
        g *= growth   # cada producto crece 28% sobre el anterior

    xs, x = [], 0.0
    for p in prepared:
        xs.append(x)
        x += p["sw"] * (1 - overlap)

    ys, cursor_y = [], 0.0
    for i, p in enumerate(prepared):
        if i > 0:
            cursor_y -= p["sh"] * step_up   # cada uno sube un poco sobre el anterior
        ys.append(cursor_y)

    # todos con el MISMO tilt_deg (-14°) — no abanico, una sola dirección
    # (ver layout.py:437-467 para el armado final del canvas/items)
```

Código completo: [`layout.py:395-467`](../layout.py#L395).

**Radial** — alturas y ángulos variados por posición, como si irradiaran desde
un punto central (no una única línea de base):

```python
# layout.py:470-549
def compute_layout_radial(
    images: List[dict],
    base_height: int = 760,
    max_vertical_boost: float = 1.20,
    overlap: float = -0.06,   # negativo = separación en vez de solapamiento
    max_tilt: float = 22.0,
    height_jitter: float = 0.12,
    **_ignored,
) -> dict:
    n = len(images)
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
    # (posicionamiento final en layout.py:503-549)
```

Código completo: [`layout.py:470-549`](../layout.py#L470).

**Flotante** — cada producto a su propia altura, sin línea de base compartida;
la sombra se achica/desvanece según qué tan "lejos" flota cada uno:

```python
# layout.py:552-638
FLOAT_AMPLITUDE = 0.30   # cuánto puede subir/bajar cada producto (fracción de la altura máx.)
FLOAT_TILT_MAX = 8.0     # inclinación leve, orgánica

def compute_layout_flotante(images, base_height=760, max_vertical_boost=1.20, overlap=-0.04, **_ignored) -> dict:
    n = len(images)
    items = _scale_items(images, base_height, max_vertical_boost)
    order = sorted(range(n), key=lambda i: items[i].sh, reverse=True)

    prepared = []
    for rank, i in enumerate(order):
        it = items[i]
        phase = rank * 2.4   # onda determinística (ángulo ~dorado), no simétrica
        yfrac = FLOAT_AMPLITUDE * math.sin(phase)
        # La inclinación se DERIVA de cuánto sube (no de una fase independiente):
        # más alto = más inclinado, como si girara al flotar.
        rot = FLOAT_TILT_MAX * (yfrac / FLOAT_AMPLITUDE)
        prepared.append({"item": it, "yfrac": yfrac, "rotation": rot})
    # (posicionamiento final + float_drop por producto en layout.py:588-638)
```

Código completo: [`layout.py:552-638`](../layout.py#L552).

---

## Parte 2 — Propuestas CON IA

### 2.1 El contrato: una llamada, un JSON, seis propuestas

Al pedir "Propuestas IA" se hace **una sola** llamada de visión (una imagen por
producto) que devuelve este JSON fijo, sin importar el proveedor:

```json
{
  "order": ["PRODUCTO_A", "PRODUCTO_B", "..."],
  "hero": "PRODUCTO_A",
  "sizes": {"PRODUCTO_A": 1.0, "PRODUCTO_B": 0.7},
  "detail_crop": {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.25},
  "free_layout_options": [
    {"label": "Dinámica", "layout": {"PRODUCTO_A": {"cx": 0.5, "cy": 0.45, "rotation": 12.0}, "...": {}}},
    {"label": "Elegante", "layout": {"...": {}}},
    {"label": "Editorial", "layout": {"...": {}}}
  ],
  "reasoning": "..."
}
```

Ese único análisis se reusa para armar **hasta 6 tarjetas** en la UI sin volver
a llamar a la IA — ver [`main.py:582-678`](../main.py#L582) (endpoint
`/api/bodegon/ai-proposal`):

| Tarjeta | De dónde sale |
|---|---|
| IA · Equilibrado, IA · Profundidad, IA · Impacto | `order` + `sizes` → mismo motor de la Parte 1 (§2.3) |
| IA · Libre — Dinámica / Elegante / Editorial | Una por cada entrada de `free_layout_options` (§2.4) |

### 2.2 El prompt exacto que recibe la IA

Esto es literalmente "qué le pide el código a la IA" — el texto completo que se
envía junto con las fotos:

```python
# ai_proposal.py:60-122
def _build_prompt(names: list) -> str:
    return f"""Eres un experto en diseño visual de bodegones comerciales y fotografía de producto.

Analiza los {len(names)} productos mostrados y determina el orden óptimo para componer un bodegón visualmente atractivo.

Criterios de evaluación:
1. Producto "héroe": el más llamativo o icónico va en posición prominente (centro o primer plano)
2. Equilibrio de color: evita agrupar tonos similares, busca contraste y armonía
3. Contraste de formas: alterna productos verticales y horizontales para crear ritmo
4. Peso visual: distribuye productos de apariencia "pesada" (oscuros, grandes) con los más "ligeros"
5. Flujo natural: el ojo del espectador debe moverse fluidamente por la composición

TAMAÑO REAL (muy importante): estima el tamaño físico real de cada producto según lo que ES
(p. ej. una botella de 1L es mucho más grande que un serum de 30ml o una barra de labial).
NO te bases en el tamaño del recorte de la imagen, sino en el tamaño real del producto en la vida real.
Asigna a cada producto un valor de tamaño relativo entre 0.3 y 1.0, donde 1.0 = el producto más
grande del conjunto y los demás en proporción (un producto que mide la mitad de alto ≈ 0.5).

ZOOM DE DETALLE: además, mirando específicamente el producto "héroe", identifica una zona
pequeña con contenido interesante para un close-up ampliado (un logo, un sello, texto
distintivo, un ícono) — NO un área lisa/vacía sin nada que mostrar. [...]

COMPOSICIÓN LIBRE (lo más importante de esta tarea, y lo que más se nota que lo hizo una IA
—no una fórmula fija—): actuá como director de arte y proponé TRES composiciones fotográficas
profesionales completas, cada una con una intención bien distinta entre sí (nunca variaciones
menores de la misma idea):
  1. "Dinámica": máxima energía — superposición fuerte, ángulos marcados y contrastantes,
     tamaños con mucha diferencia entre el héroe y el resto.
  2. "Elegante": minimalista — más espacio en blanco entre productos, ángulos sutiles o nulos,
     composición serena tipo producto premium.
  3. "Editorial": asimétrica y con ritmo — como una revista de moda: tamaños contrastados,
     algún producto bien arriba o abajo del resto, no todos a la misma altura.
Para cada una, y para cada producto, [...] dame:
  - cx, cy: el CENTRO del producto [...] entre 0.12 y 0.88 en ambos ejes [...] con 2 productos,
    sus cx NUNCA deben quedar a menos de ~0.2 de distancia entre sí [...]
  - rotation: inclinación en grados, de -35 a 35 (en "Elegante" pueden ser todos cercanos a 0).
Las tres deben poder distinguirse a simple vista entre sí [...]

Responde ÚNICAMENTE con este JSON (sin texto adicional, sin markdown): [...]"""
```

Texto completo (con las instrucciones de `detail_crop` y el JSON de salida sin
recortar): [`ai_proposal.py:60-122`](../ai_proposal.py#L60).

### 2.3 Camino A — la IA elige orden/tamaño, el motor de la Parte 1 hace el resto

`ai`, `ai_depth` e `ai_impacto` **no tienen código de layout propio**: son
`auto`, `profundidad` e `impacto` (mismas entradas en `DEPTH_PARAMS`, ver §1.1),
alimentadas con el orden y el tamaño relativo que decidió la IA en vez del
orden por altura de foto. La única diferencia real está en `main.py`, al
armar la lista `ordered` antes de llamar al mismo `compute_layout_any`:

```python
# main.py:307-311 — las 3 tarjetas fijas ✦ IA
AI_VARIANT_DEFS = [
    ("ai",             "IA · Equilibrado",  "Orden y tamaños inteligentes"),
    ("ai_depth",       "IA · Profundidad",  "Héroe al frente + solapamiento"),
    ("ai_impacto",     "IA · Impacto",      "Abanico dinámico tipo anuncio"),
]

# main.py:617-656 — dentro de /api/bodegon/ai-proposal
name_to_img = {img["name"]: img for img in info["images"]}
sizes = suggestion.get("sizes", {})
ordered = []
for n in suggestion["order"]:          # ← el ORDEN lo puso la IA
    if n in name_to_img:
        img = dict(name_to_img[n])
        img["scale"] = float(sizes.get(n, 1.0))   # ← el TAMAÑO relativo lo puso la IA
        ordered.append(img)

def _ai_layout(strategy_id: str):
    return compute_layout_any(       # ← EXACTAMENTE el mismo motor de la Parte 1
        images=ordered, sort_strategy=strategy_id, ...
    )

variants = [
    {"id": vid, "label": label, "desc": desc, "layout": _ai_layout(vid)}
    for vid, label, desc in AI_VARIANT_DEFS
]
```

Por eso `SORT_STRATEGIES['ai'] = list` (§1.1): la función de orden es la
identidad — no reordena nada porque ya viene ordenado por la IA. Lo único que
cambió respecto a la versión sin IA es el **dato de entrada**, no el código
geométrico.

> Nota de implementación: `layout.py` todavía define `ai_shadow`, `ai_cruce`,
> `ai_fila_recta`, `ai_inclinado_derecha/izquierda` con el mismo patrón — pero
> `main.py` ya no las expone como tarjetas (se sacaron de `AI_VARIANT_DEFS`
> porque, al ser fórmula fija, se veían casi idénticas a su versión sin IA sin
> aportar nada visible a cambio del costo de la llamada). El código sigue
> ahí, funcional, por si se reactivan.

### 2.4 Camino B — "IA · Libre": la IA propone la geometría directamente

Acá sí hay código de layout exclusivo. La IA no elige entre parámetros
predefinidos: da `cx`, `cy` y `rotation` por producto, sobre un lienzo
imaginario de 0.0–1.0, y este código solo traduce esas fracciones a píxeles:

```python
# layout.py:641-757
MIN_SEPARATION_FRAC = 0.42

def _enforce_min_separation(placed, min_frac: float = MIN_SEPARATION_FRAC):
    """Red de seguridad determinística: si dos productos quedan con los
    centros demasiado juntos (uno tapando casi por completo al otro), los
    separa horizontalmente lo mínimo necesario. Solo toca X — Y y rotación
    quedan exactamente como los propuso la IA."""
    placed = list(placed)
    order = sorted(range(len(placed)), key=lambda i: placed[i][1])
    for a, b in zip(order, order[1:]):
        it_a, cx_a, cy_a, rot_a = placed[a]
        it_b, cx_b, cy_b, rot_b = placed[b]
        min_gap = min_frac * (it_a.sw + it_b.sw) / 2
        gap = cx_b - cx_a
        if gap < min_gap:
            push = (min_gap - gap) / 2
            placed[a] = (it_a, cx_a - push, cy_a, rot_a)
            placed[b] = (it_b, cx_b + push, cy_b, rot_b)
    return placed


def compute_layout_ai_freeform(images, free_layout, base_height=760, max_vertical_boost=1.20, **_ignored) -> dict:
    items = _scale_items(images, base_height, max_vertical_boost)
    # Lienzo de referencia derivado del tamaño YA ESCALADO real de los
    # productos (no de base_height a secas) — si no, con pocos productos
    # altos el cuadrado de referencia queda enorme y las posiciones 0-1 de
    # la IA terminan dispersas, con huecos en vez de la superposición que
    # pide el prompt.
    total_w = sum(it.sw for it in items)
    max_h = max(it.sh for it in items)
    ref_side = max(max_h, total_w * 0.6) * 1.15

    placed = []
    for it in items:
        pos = free_layout.get(it.name, {"cx": 0.5, "cy": 0.5, "rotation": 0.0})
        cx = float(pos.get("cx", 0.5)) * ref_side
        cy = float(pos.get("cy", 0.5)) * ref_side
        rot = float(pos.get("rotation", 0.0))
        placed.append((it, cx, cy, rot))

    placed = _enforce_min_separation(placed)   # ← el único ajuste que NO viene de la IA

    # bounding box + orden de pintado por tamaño (el más grande al frente) —
    # ver layout.py:718-757 para el armado final del canvas
```

Código completo: [`layout.py:641-757`](../layout.py#L641).

### 2.5 La capa de validación: nunca confiar en el JSON crudo del modelo

Antes de que cualquier número de la IA llegue a un layout, se clampea a
rangos seguros y se completan los campos que falten — así una respuesta
imperfecta (un producto omitido, una rotación fuera de rango) no rompe el
render:

```python
# ai_proposal.py:167-185
def _normalize_free_layout(free_layout, names):
    free_layout = free_layout if isinstance(free_layout, dict) else {}
    out = {}
    for i, n in enumerate(names):
        pos = free_layout.get(n)
        pos = pos if isinstance(pos, dict) else {}
        try:
            cx = max(0.08, min(0.92, float(pos.get("cx", 0.5))))
            cy = max(0.08, min(0.92, float(pos.get("cy", 0.5))))
            rot = max(-35.0, min(35.0, float(pos.get("rotation", 0.0))))
        except (TypeError, ValueError):
            cx, cy, rot = 0.5, 0.5, 0.0   # producto sin dato usable → posición neutra
        out[n] = {"cx": round(cx, 3), "cy": round(cy, 3), "rotation": round(rot, 1)}
    return out
```

Mismo criterio en `_normalize_sizes` (clampea a 0.3–1.0,
[`ai_proposal.py:204-227`](../ai_proposal.py#L204)), `_normalize_detail_crop`
(recuadro dentro de 0–1, [`ai_proposal.py:188-201`](../ai_proposal.py#L188)) y
`_parse_result` (si el JSON viene truncado por límite de tokens, avisa con un
mensaje claro en vez de un traceback crudo,
[`ai_proposal.py:125-148`](../ai_proposal.py#L125)).

El presupuesto de tokens de la respuesta tampoco es fijo — escala con la
cantidad de productos (más productos = JSON más largo, sobre todo por las 3
`free_layout_options`):

```python
# ai_proposal.py:29-37
def _dynamic_max_tokens(n: int, base: int = 500, per_item: int = 140, cap: int = 8000) -> int:
    return min(cap, base + per_item * max(1, n))
```

### 2.6 Los 4 proveedores — mismo contrato, distinto backend

`analyze_products()` es el único punto de entrada; resuelve el proveedor
según `.env` (`AI_PROVIDER`) y delega:

```python
# ai_proposal.py:384-432
def analyze_products(images, groq_key=None, gemini_key=None, anthropic_key=None, openai_key=None, provider=None) -> dict:
    images = _available_images(images)
    if not provider:
        provider = ("groq" if groq_key else
                    "gemini" if gemini_key else
                    "claude" if anthropic_key else "openai")
    if provider == "groq":    return _analyze_groq(images, groq_key)
    if provider == "gemini":  return _analyze_gemini(images, gemini_key)
    if provider == "claude":  return _analyze_claude(images, anthropic_key)
    if provider == "openai":  return _analyze_openai(images, openai_key)
```

Las 4 implementaciones arman el mismo contenido (texto del nombre + foto en
base64 por producto + `_build_prompt()` al final) con la sintaxis propia de
cada SDK. Esta es la activa hoy por `.env` (`AI_PROVIDER=openai`):

```python
# ai_proposal.py:352-380
def _analyze_openai(images: list, api_key: str) -> dict:
    from openai import OpenAI
    names = [img["name"] for img in images]
    client = OpenAI(api_key=api_key)

    content = []
    for item in images:
        content.append({"type": "text", "text": f"[{item['name']}]"})
        fp = item.get("filepath", "")
        if fp and Path(fp).exists():
            b64 = base64.standard_b64encode(_to_jpeg_bytes(fp)).decode()
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            })
    content.append({"type": "text", "text": _build_prompt(names)})

    response = client.chat.completions.create(
        model="gpt-5.1",
        messages=[{"role": "user", "content": content}],
        temperature=0.4,
        max_completion_tokens=_dynamic_max_tokens(len(names)),
        response_format={"type": "json_object"},
    )
    return _parse_result(response.choices[0].message.content, names)
```

Los otros tres, mismo patrón, distinto SDK/modelo:
[`_analyze_groq`](../ai_proposal.py#L310) (`qwen/qwen3.6-27b`, máx. 5 imágenes
por límite de Groq, `reasoning_effort="none"` porque el modo "pensamiento"
del modelo agota el budget de tokens antes de responder),
[`_analyze_gemini`](../ai_proposal.py#L241) (`gemini-2.5-flash`),
[`_analyze_claude`](../ai_proposal.py#L278) (`claude-sonnet-4-6`).

---

## Resumen — de dónde sale cada propuesta

| Propuesta | Código | ¿Llama a la IA? |
|---|---|---|
| Auto, Por área, Alternado, Centrado, Uniforme, Dinámico, Sombra | `compute_layout()` + `SORT_STRATEGIES` | No |
| Profundidad, Héroe XL, Escalonada | `compute_layout()` + `DEPTH_PARAMS` (hero_scale/falloff) | No |
| Impacto | `compute_layout()`, `tilt_mode='fan'` | No |
| Cruce | `compute_layout()`, `tilt_mode='converge'` | No |
| Inclinado derecha/izquierda | `compute_layout()`, `tilt_mode='uniform'` + ángulo del slider | No |
| Fila recta | `compute_layout()`, `center_hero=False` | No |
| Cascada | `compute_layout_cascada()` | No |
| Radial | `compute_layout_radial()` | No |
| Flotante | `compute_layout_flotante()` | No |
| IA · Equilibrado / Profundidad / Impacto | `compute_layout()` (mismo código que sus pares sin IA) + orden/tamaño de `analyze_products()` | Sí — orden y tamaño |
| IA · Libre (Dinámica/Elegante/Editorial) | `compute_layout_ai_freeform()` + `_enforce_min_separation()` | Sí — posición y ángulo completos |

En el navegador, las 18 propuestas sin IA además tienen su espejo en
JavaScript para la vista previa instantánea: `computeLayoutJS`
([`index.html:1136`](../static/index.html#L1136)),
`computeCascadaJS`/`computeRadialJS`/`computeFlotanteJS`
([`index.html:1287-1369`](../static/index.html#L1287)). Las tarjetas ✦ IA no
se recalculan en JS — llegan ya resueltas del servidor en la respuesta de
`/api/bodegon/ai-proposal`, porque dependen de la llamada al modelo de visión.
