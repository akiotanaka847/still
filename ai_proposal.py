"""
Módulo de propuesta de layout con IA.
Analiza los productos (visión) y sugiere el orden óptimo para el bodegón.

Proveedores soportados:
  - Groq    (groq)           → modelo qwen/qwen3.6-27b
  - Gemini  (google-genai)   → modelo gemini-2.5-flash
  - Claude  (anthropic)      → modelo claude-sonnet-4-6
  - OpenAI  (openai)         → modelo gpt-5.1

Los cuatro devuelven el MISMO contrato: {order, hero, sizes, detail_crop, reasoning}.
El fondo sigue siendo transparente: la IA solo decide el ORDEN/TAMAÑO/zoom, el
motor geométrico (layout.py) compone igual que siempre.
"""
import base64
import io
import json
import math
import re
from pathlib import Path

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


# ─── Helpers compartidos ──────────────────────────────────────────────────────
def _dynamic_max_tokens(n: int, base: int = 800, per_item: int = 220, cap: int = 16000) -> int:
    """El JSON de respuesta escala con la cantidad de productos — sobre todo
    por 'free_layout_options' (3 composiciones libres, cada una con
    cx/cy/rotation POR PRODUCTO). Con un límite fijo, un grupo grande corta
    la respuesta a la mitad de una entrada y json.loads() falla con
    'Expecting X delimiter' — no es que la IA "se equivoque", es que no la
    dejamos terminar de escribir. Se calcula un presupuesto proporcional a
    la cantidad de productos en vez de un número fijo pensado para pocos.

    Los defaults tienen que alcanzar sin problema hasta MAX_FILES (60,
    ver main.py) — el techo real de productos por grupo que permite la
    app. per_item=220 (antes 140) porque el nombre de cada producto se
    repite hasta 5 veces en el JSON (en 'order', 'sizes' y las 3 de
    'free_layout_options'), y con nombres largos tipo código de barras
    ('7501000111602_F_1675808320287') eso pesa bastante más de lo que
    parece a simple vista — se confirmó truncando en la práctica con un
    grupo de 60 productos reales antes de subir este ajuste."""
    return min(cap, base + per_item * max(1, n))


def _to_jpeg_bytes(filepath: str, max_size: int = 512) -> bytes:
    """Redimensiona una imagen y la devuelve como JPEG (bytes) para la API de visión."""
    with Image.open(filepath) as img:
        img.thumbnail((max_size, max_size), Image.LANCZOS)
        if img.mode in ("RGBA", "LA", "P"):
            if img.mode == "P":
                img = img.convert("RGBA")
            bg = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode in ("RGBA", "LA"):
                bg.paste(img, mask=img.split()[-1])
            else:
                bg.paste(img)
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()


def _product_brief(images_or_names: list) -> tuple[list, str]:
    """Resumen compacto de cada producto para que la IA piense composición
    con siluetas reales, no solo nombres. Acepta la lista completa de
    imágenes o, por compatibilidad, una lista de nombres."""
    names = []
    rows = []
    for item in images_or_names:
        if isinstance(item, dict):
            name = item.get("name", "")
            w = max(1, int(item.get("width") or item.get("w") or 1))
            h = max(1, int(item.get("height") or item.get("h") or 1))
            ratio = w / h
            if ratio < 0.72:
                shape = "vertical/alto"
            elif ratio > 1.35:
                shape = "horizontal/ancho"
            else:
                shape = "compacto/cuadrado"
            rows.append(f"- {name}: {shape}, proporción visual aprox. {w}:{h}")
        else:
            name = str(item)
            rows.append(f"- {name}")
        names.append(name)
    return names, "\n".join(rows)


def _build_prompt(images_or_names: list) -> str:
    names, product_brief = _product_brief(images_or_names)
    return f"""Eres un experto en diseño visual de bodegones comerciales y fotografía de producto.

Analiza los {len(names)} productos mostrados y determina el orden óptimo para componer un bodegón visualmente atractivo.

Criterios de evaluación:
1. Producto "héroe": el más llamativo o icónico va en posición prominente (centro o primer plano)
2. Equilibrio de color: evita agrupar tonos similares, busca contraste y armonía
3. Contraste de formas: alterna productos verticales y horizontales para crear ritmo
4. Peso visual: distribuye productos de apariencia "pesada" (oscuros, grandes) con los más "ligeros"
5. Flujo natural: el ojo del espectador debe moverse fluidamente por la composición

Productos y siluetas aproximadas:
{product_brief}

TAMAÑO REAL (muy importante): estima el tamaño físico real de cada producto según lo que ES
(p. ej. una botella de 1L es mucho más grande que un serum de 30ml o una barra de labial).
NO te bases en el tamaño del recorte de la imagen, sino en el tamaño real del producto en la vida real.
Asigna a cada producto un valor de tamaño relativo entre 0.3 y 1.0, donde 1.0 = el producto más
grande del conjunto y los demás en proporción (un producto que mide la mitad de alto ≈ 0.5).

ZOOM DE DETALLE: además, mirando específicamente el producto "héroe", identifica una zona
pequeña con contenido interesante para un close-up ampliado (un logo, un sello, texto
distintivo, un ícono) — NO un área lisa/vacía sin nada que mostrar. Da su ubicación como
un recuadro en fracciones de 0.0 a 1.0 relativas SOLO al recuadro del héroe (x,y = esquina
superior izquierda del recuadro; w,h = ancho/alto), pensado para una burbuja circular de
zoom de ~30% de ancho x 20% de alto del producto.

COMPOSICIÓN LIBRE (lo más importante de esta tarea, y lo que más se nota que lo hizo una IA
—no una fórmula fija—): actúa como director de arte y propone TRES composiciones fotográficas
profesionales completas y COHERENTES. No repartas productos al azar por el lienzo: todos deben
sentirse parte de una misma escena, con una base visual común, jerarquía clara y aire suficiente
para reconocer cada etiqueta. Cada composición debe tener una intención bien distinta entre sí
(nunca variaciones menores de la misma idea):
  1. "Dinámica": máxima energía — superposición fuerte, ángulos marcados y contrastantes,
     tamaños con mucha diferencia entre el héroe y el resto.
  2. "Elegante": minimalista — más espacio en blanco entre productos, ángulos sutiles o nulos,
     composición serena tipo producto premium.
  3. "Editorial": asimétrica y con ritmo — como una revista de moda: tamaños contrastados,
     algún producto bien arriba o abajo del resto, no todos a la misma altura.

Reglas de composición por cantidad:
- Si hay 2 productos: NO los pongas uno encima del otro ni pegados al centro. Haz un dúo claro:
  héroe ligeramente dominante y acompañante visible, con centros separados horizontalmente.
- Si hay 3 a 5 productos: crea una familia compacta tipo triángulo/pirámide: héroe cerca del
  centro, secundarios a los lados o levemente atrás. Deben verse unidos, no dispersos.
- Si hay 6 a 9 productos: agrúpalos en una composición de catálogo/editorial con 2 niveles
  visuales o arco suave. Evita una fila plana interminable y evita una nube caótica.
- Si hay 10 o más productos: prioriza orden legible: dos filas o arco amplio, grupos por tamaño
  y color, con el héroe anclando el centro. La coherencia importa más que la extravagancia.

Reglas duras de visibilidad:
- Ningún producto puede quedar casi totalmente tapado por otro.
- No uses el mismo cx/cy para varios productos.
- En cualquier par de productos, separa los centros al menos 0.14 en distancia normalizada
  (0.20 o más si solo hay 2 o 3 productos), salvo solapamientos leves e intencionales.
- Mantén los centros entre 0.14 y 0.86 cuando sea posible; usa 0.10-0.90 solo para extremos.
Para cada una, y para cada producto, sobre un lienzo cuadrado imaginario de 0.0 a 1.0 en cada
eje (0,0 = arriba-izquierda, 1,1 = abajo-derecha), dame:
  - cx, cy: el CENTRO del producto. El héroe suele ir más al centro y grande; los secundarios
    pueden ir más chicos, superpuestos, a los costados o a distinta altura — pero mantén todos
    entre 0.12 y 0.88 en ambos ejes para que ninguno quede cortado contra el borde. IMPORTANTE:
    con 2 productos, sus cx NUNCA deben quedar a menos de ~0.2 de distancia entre sí (p. ej. 0.35
    y 0.65, no 0.48 y 0.52) — si quedan casi en el mismo punto, uno tapa por completo al otro y
    se pierde el producto. En "Elegante" en particular, sepáralos con criterio generoso: se busca
    espacio en blanco entre ellos, NO amontonados.
  - rotation: inclinación en grados, de -35 a 35 (en "Elegante" pueden ser todos cercanos a 0).
Las tres deben poder distinguirse a simple vista entre sí — si dos quedan parecidas, no cumpliste
la consigna.

Nombres exactos de los productos: {json.dumps(names)}

Responde ÚNICAMENTE con este JSON (sin texto adicional, sin markdown):
{{
  "order": [lista con los {len(names)} nombres exactos en el orden sugerido],
  "hero": "nombre del producto más prominente",
  "sizes": {{"NOMBRE_EXACTO": tamaño_relativo_0.3_a_1.0, ...}} para los {len(names)} productos,
  "detail_crop": {{"x": 0.0_a_1.0, "y": 0.0_a_1.0, "w": 0.0_a_1.0, "h": 0.0_a_1.0}},
  "free_layout_options": [
    {{"label": "Dinámica", "layout": {{"NOMBRE_EXACTO": {{"cx":0.0_a_1.0,"cy":0.0_a_1.0,"rotation":-35_a_35}}, ...}} para los {len(names)} productos}},
    {{"label": "Elegante", "layout": {{...}} para los {len(names)} productos}},
    {{"label": "Editorial", "layout": {{...}} para los {len(names)} productos}}
  ],
  "reasoning": "explicación de 1-2 oraciones en español que mencione el orden y los tamaños"
}}"""


def _parse_result(raw: str, names: list) -> dict:
    """Extrae y valida el JSON de la respuesta, completando nombres faltantes."""
    raw = (raw or "").strip()
    # Quitar cercas de código markdown si vinieran
    raw = re.sub(r"^```(?:json)?", "", raw).strip()
    raw = re.sub(r"```$", "", raw).strip()
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        raise ValueError(
            "La IA devolvió una respuesta incompleta o vacía "
            "(probable límite de tokens). Reintenta o usa menos productos."
        )
    result = json.loads(m.group(0))

    valid = set(names)
    result["order"] = [n for n in result.get("order", []) if n in valid]
    missing = [n for n in names if n not in set(result["order"])]
    result["order"].extend(missing)
    result.setdefault("hero", result["order"][0] if result["order"] else "")
    if result["hero"] not in names:
        result["hero"] = result["order"][0] if result["order"] else ""
    result.setdefault("reasoning", "")
    result["sizes"] = _normalize_sizes(result.get("sizes", {}), names)
    result["detail_crop"] = _normalize_detail_crop(result.get("detail_crop"))
    result["free_layout_options"] = _normalize_free_layout_options(
        result.get("free_layout_options"), names, result.get("hero")
    )
    return result


def _style_key(label: str, option_index: int = 0) -> str:
    txt = (label or "").lower()
    if "eleg" in txt or option_index == 1:
        return "elegant"
    if "editor" in txt or option_index == 2:
        return "editorial"
    return "dynamic"


def _ordered_for_layout(names: list, hero: str = None) -> list:
    if hero in names:
        return [hero] + [n for n in names if n != hero]
    return list(names)


def _fallback_free_layout(names: list, style: str = "dynamic", hero: str = None) -> dict:
    """Composición determinista de respaldo cuando la IA devuelve centros
    inutilizables. No intenta reemplazar el criterio artístico del modelo:
    solo evita que la tarjeta 'IA · Libre' termine como una pila ilegible."""
    ordered = _ordered_for_layout(names, hero)
    n = len(ordered)
    if n == 0:
        return {}
    if n == 1:
        return {ordered[0]: {"cx": 0.5, "cy": 0.52, "rotation": 0.0}}

    out = {}
    if style == "elegant":
        if n <= 4:
            xs = [0.50] if n == 1 else [0.24 + i * (0.52 / (n - 1)) for i in range(n)]
            for i, name in enumerate(ordered):
                out[name] = {"cx": xs[i], "cy": 0.52 + (0.025 if i % 2 else 0.0), "rotation": 0.0}
        else:
            cols = min(5, max(3, round(n ** 0.5) + 1))
            rows = (n + cols - 1) // cols
            for i, name in enumerate(ordered):
                row, col = divmod(i, cols)
                denom_x = max(1, cols - 1)
                denom_y = max(1, rows - 1)
                out[name] = {
                    "cx": 0.20 + 0.60 * (col / denom_x),
                    "cy": 0.38 + 0.26 * (row / denom_y),
                    "rotation": 0.0,
                }
        return {k: {kk: round(vv, 3) if kk != "rotation" else round(vv, 1)
                    for kk, vv in v.items()} for k, v in out.items()}

    if style == "editorial":
        seed = [
            (0.47, 0.56, -4), (0.66, 0.39, 10), (0.30, 0.45, -12),
            (0.70, 0.66, 7), (0.38, 0.72, 14), (0.55, 0.30, -8),
            (0.21, 0.62, 6), (0.82, 0.50, -10), (0.50, 0.80, 3),
        ]
    else:
        seed = [
            (0.50, 0.58, 0), (0.35, 0.52, -14), (0.65, 0.52, 14),
            (0.27, 0.67, 9), (0.73, 0.67, -9), (0.42, 0.39, 17),
            (0.58, 0.39, -17), (0.20, 0.55, -6), (0.80, 0.55, 6),
        ]

    for i, name in enumerate(ordered):
        if i < len(seed):
            cx, cy, rot = seed[i]
        else:
            # Más productos: arco ordenado alrededor del centro, legible y compacto.
            t = (i - len(seed)) / max(1, n - len(seed))
            angle = math.pi * (0.10 + 0.80 * t)
            radius_x = 0.36
            radius_y = 0.28
            cx = 0.50 + math.cos(angle) * radius_x
            cy = 0.56 + math.sin(angle) * radius_y
            rot = max(-18, min(18, (0.5 - t) * 32))
        out[name] = {"cx": cx, "cy": cy, "rotation": rot}

    return {k: {kk: round(vv, 3) if kk != "rotation" else round(vv, 1)
                for kk, vv in v.items()} for k, v in out.items()}


def _layout_quality(layout: dict, names: list) -> tuple[float, float, float]:
    pts = [(layout[n]["cx"], layout[n]["cy"]) for n in names if n in layout]
    if len(pts) < 2:
        return 1.0, 1.0, 1.0
    min_dist = min(
        ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
        for i, (ax, ay) in enumerate(pts)
        for bx, by in pts[i + 1:]
    )
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min_dist, max(xs) - min(xs), max(ys) - min(ys)


def _polish_free_layout(layout: dict, names: list, style: str = "dynamic", hero: str = None) -> dict:
    n = len(names)
    if n < 2:
        return layout

    min_dist, x_span, y_span = _layout_quality(layout, names)
    severe_cluster = min_dist < 0.055 or (x_span < 0.16 and y_span < 0.16)
    if severe_cluster:
        return _fallback_free_layout(names, style, hero)

    target = 0.20 if n <= 3 else 0.16 if n <= 6 else 0.12
    if style == "elegant":
        target += 0.04 if n <= 6 else 0.02
    elif style == "dynamic":
        target -= 0.02

    pts = {name: [layout[name]["cx"], layout[name]["cy"], layout[name]["rotation"]] for name in names}
    for _ in range(24):
        moved = False
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                ax, ay, _ = pts[a]
                bx, by, _ = pts[b]
                dx, dy = bx - ax, by - ay
                dist = (dx * dx + dy * dy) ** 0.5
                if dist >= target:
                    continue
                if dist < 1e-6:
                    dx, dy, dist = 1.0, 0.0, 1.0
                push = (target - dist) / 2
                ux, uy = dx / dist, dy / dist
                pts[a][0] -= ux * push
                pts[a][1] -= uy * push
                pts[b][0] += ux * push
                pts[b][1] += uy * push
                moved = True
        for name in names:
            pts[name][0] = max(0.10, min(0.90, pts[name][0]))
            pts[name][1] = max(0.10, min(0.90, pts[name][1]))
        if not moved:
            break

    polished = {
        name: {"cx": round(pts[name][0], 3), "cy": round(pts[name][1], 3), "rotation": round(pts[name][2], 1)}
        for name in names
    }
    _, _, y_span = _layout_quality(polished, names)
    if style != "elegant" and n >= 3 and y_span < 0.08:
        ordered = _ordered_for_layout(names, hero)
        offsets = [0.00, -0.07, 0.06, -0.03, 0.04]
        for i, name in enumerate(ordered):
            polished[name]["cy"] = round(max(0.10, min(0.90, polished[name]["cy"] + offsets[i % len(offsets)])), 3)
    return polished


def _normalize_free_layout_options(options, names, hero: str = None):
    """Valida la lista de composiciones libres (cada una con su 'label' y su
    'layout' posición/rotación por producto). Si la IA no devolvió nada
    usable, la lista queda vacía y 'IA · Libre' simplemente no aparece —
    mejor eso que forzar 3 composiciones inventadas sin criterio real."""
    if not isinstance(options, list):
        return []
    out = []
    for i, opt in enumerate(options[:3]):   # tope de 3, por costo/tamaño de UI
        if not isinstance(opt, dict):
            continue
        label = str(opt.get("label") or f"Libre {i + 1}").strip()[:40] or f"Libre {i + 1}"
        style = _style_key(label, i)
        out.append({"label": label, "layout": _normalize_free_layout(opt.get("layout"), names, style, hero)})
    return out


def _normalize_free_layout(free_layout, names, style: str = "dynamic", hero: str = None):
    """Valida la composición libre (posición/rotación por producto) que
    propone la IA. Si un producto falta o vino con datos inválidos, se le
    asigna una posición central neutra en vez de descartar toda la
    propuesta — así 'IA · Libre' siempre tiene algo razonable donde pintar
    incluso si la IA se equivocó en uno solo."""
    free_layout = free_layout if isinstance(free_layout, dict) else {}
    out = {}
    for i, n in enumerate(names):
        pos = free_layout.get(n)
        pos = pos if isinstance(pos, dict) else {}
        try:
            fallback = _fallback_free_layout(names, style, hero).get(n, {"cx": 0.5, "cy": 0.5, "rotation": 0.0})
            cx = max(0.08, min(0.92, float(pos.get("cx", fallback["cx"]))))
            cy = max(0.08, min(0.92, float(pos.get("cy", fallback["cy"]))))
            rot = max(-35.0, min(35.0, float(pos.get("rotation", 0.0))))
        except (TypeError, ValueError):
            fallback = _fallback_free_layout(names, style, hero).get(n, {"cx": 0.5, "cy": 0.5, "rotation": 0.0})
            cx, cy, rot = fallback["cx"], fallback["cy"], fallback["rotation"]
        out[n] = {"cx": round(cx, 3), "cy": round(cy, 3), "rotation": round(rot, 1)}
    return _polish_free_layout(out, names, style, hero)


def _normalize_detail_crop(crop):
    """Valida el recuadro de zoom que sugiere la IA. Si falta o es inválido,
    devuelve None — el renderer cae automáticamente al detector por bordes/
    contraste (ver renderer._auto_detail_region), no hace falta un default acá."""
    if not isinstance(crop, dict):
        return None
    try:
        x = max(0.0, min(0.9, float(crop.get("x", 0))))
        y = max(0.0, min(0.9, float(crop.get("y", 0))))
        w = max(0.05, min(1.0 - x, float(crop.get("w", 0))))
        h = max(0.05, min(1.0 - y, float(crop.get("h", 0))))
    except (TypeError, ValueError):
        return None
    return {"x": round(x, 3), "y": round(y, 3), "w": round(w, 3), "h": round(h, 3)}


def _normalize_sizes(sizes: dict, names: list) -> dict:
    """Normaliza los tamaños de la IA: el mayor = 1.0, resto en proporción, mín 0.3."""
    sizes = sizes or {}
    raw = {}
    for n in names:
        v = sizes.get(n)
        if v is None:  # intento case-insensitive
            for k, val in sizes.items():
                if str(k).upper() == n.upper():
                    v = val
                    break
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = None
        raw[n] = v if (v and v > 0) else None

    present = [v for v in raw.values() if v]
    mx = max(present) if present else 1.0
    out = {}
    for n in names:
        v = raw[n] if raw[n] else 0.7 * mx   # neutro si la IA no lo dio
        out[n] = round(max(0.3, min(1.0, v / mx)), 3)
    return out


def _available_images(images: list) -> list:
    """Filtra los productos que tienen archivo accesible en disco."""
    out = []
    for item in images:
        fp = item.get("filepath", "")
        if fp and Path(fp).exists():
            out.append(item)
    return out


# ─── Backend: Gemini ──────────────────────────────────────────────────────────
def _analyze_gemini(images: list, api_key: str) -> dict:
    try:
        from google import genai
        from google.genai import types
    except ImportError as e:
        raise RuntimeError(
            "Falta el SDK de Gemini. Ejecuta: pip install google-genai"
        ) from e

    names = [img["name"] for img in images]
    client = genai.Client(api_key=api_key)

    contents = []
    for item in images:
        contents.append(f"[{item['name']}]")
        fp = item.get("filepath", "")
        if fp and Path(fp).exists():
            contents.append(
                types.Part.from_bytes(
                    data=_to_jpeg_bytes(fp), mime_type="image/jpeg"
                )
            )
    contents.append(_build_prompt(images))

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.4,
            max_output_tokens=_dynamic_max_tokens(len(names)),
        ),
    )
    return _parse_result(response.text, names)


# ─── Backend: Claude ──────────────────────────────────────────────────────────
def _analyze_claude(images: list, api_key: str) -> dict:
    import anthropic

    names = [img["name"] for img in images]
    client = anthropic.Anthropic(api_key=api_key)

    content = []
    for item in images:
        content.append({"type": "text", "text": f"[{item['name']}]"})
        fp = item.get("filepath", "")
        if fp and Path(fp).exists():
            b64 = base64.standard_b64encode(_to_jpeg_bytes(fp)).decode()
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
            })
    content.append({"type": "text", "text": _build_prompt(images)})

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=_dynamic_max_tokens(len(names)),
        messages=[{"role": "user", "content": content}],
    )
    return _parse_result(response.content[0].text, names)


# ─── Backend: Groq (Qwen3, visión) ─────────────────────────────────────────────
# llama-4-scout (deprecado por Groq el 17/07/2026) fue reemplazado por qwen3.6-27b,
# que soporta hasta 5 imágenes por petición.
GROQ_MAX_IMAGES = 5


def _analyze_groq(images: list, api_key: str) -> dict:
    try:
        from groq import Groq
    except ImportError as e:
        raise RuntimeError("Falta el SDK de Groq. Ejecuta: pip install groq") from e

    names = [img["name"] for img in images]
    client = Groq(api_key=api_key)

    content = []
    sent = 0
    for item in images:
        content.append({"type": "text", "text": f"[{item['name']}]"})
        fp = item.get("filepath", "")
        # Solo adjuntamos imagen hasta el límite; el resto van como texto (nombre)
        if fp and Path(fp).exists() and sent < GROQ_MAX_IMAGES:
            b64 = base64.standard_b64encode(_to_jpeg_bytes(fp)).decode()
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            })
            sent += 1
    content.append({"type": "text", "text": _build_prompt(images)})

    response = client.chat.completions.create(
        model="qwen/qwen3.6-27b",
        messages=[{"role": "user", "content": content}],
        temperature=0.4,
        # Tope bajo a propósito: la cuenta free de Groq tiene un límite de
        # 8000 tokens/minuto que ya se va casi entero en las imágenes (hasta
        # GROQ_MAX_IMAGES=5 × 2048c/u) — pedir un budget grande acá dispara
        # "Request too large" en vez de arreglar el truncado. Con grupos
        # grandes, Groq va a quedar corto de por sí (limitación de su tier
        # gratuito, no de este código); para eso conviene otro proveedor.
        max_completion_tokens=_dynamic_max_tokens(len(names), base=300, per_item=90, cap=2500),
        reasoning_effort="none",  # sin esto, el modo "thinking" agota el budget de tokens
        response_format={"type": "json_object"},
    )
    return _parse_result(response.choices[0].message.content, names)


# ─── Backend: OpenAI (GPT-5.1, visión) ─────────────────────────────────────────
def _analyze_openai(images: list, api_key: str) -> dict:
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError("Falta el SDK de OpenAI. Ejecuta: pip install openai") from e

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
    content.append({"type": "text", "text": _build_prompt(images)})

    response = client.chat.completions.create(
        model="gpt-5.1",
        messages=[{"role": "user", "content": content}],
        temperature=0.4,
        max_completion_tokens=_dynamic_max_tokens(len(names)),
        response_format={"type": "json_object"},
    )
    return _parse_result(response.choices[0].message.content, names)


# ─── Dispatcher ───────────────────────────────────────────────────────────────
def analyze_products(
    images: list,
    groq_key: str = None,
    gemini_key: str = None,
    anthropic_key: str = None,
    openai_key: str = None,
    provider: str = None,
) -> dict:
    """
    Envía los thumbnails de los productos a la IA y obtiene el orden óptimo.

    provider: "groq" | "gemini" | "claude" | "openai" | None
              (auto: groq → gemini → claude → openai, según la primera key disponible).
    Returns: {order: [nombres], hero: nombre, reasoning: str}
    """
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow no instalado")

    images = _available_images(images)
    if not images:
        raise RuntimeError("No hay imágenes de productos accesibles para analizar")

    # Resolver proveedor
    if not provider:
        provider = ("groq" if groq_key else
                    "gemini" if gemini_key else
                    "claude" if anthropic_key else "openai")

    if provider == "groq":
        if not groq_key:
            raise RuntimeError("GROQ_API_KEY no configurada")
        return _analyze_groq(images, groq_key)

    if provider == "gemini":
        if not gemini_key:
            raise RuntimeError("GEMINI_API_KEY no configurada")
        return _analyze_gemini(images, gemini_key)

    if provider == "claude":
        if not anthropic_key:
            raise RuntimeError("ANTHROPIC_API_KEY no configurada")
        return _analyze_claude(images, anthropic_key)

    if provider == "openai":
        if not openai_key:
            raise RuntimeError("OPENAI_API_KEY no configurada")
        return _analyze_openai(images, openai_key)

    raise RuntimeError(f"Proveedor de IA desconocido: {provider}")


# ═══════════════════════════════════════════════════════════════════════════
# COMPOSICIÓN "A MEDIDA" — instrucción libre del usuario, llamada AISLADA
# ═══════════════════════════════════════════════════════════════════════════
# Primer intento (revertido, ver commit d612c49): meter la instrucción del
# usuario como una 4ª entrada dentro del MISMO prompt de analyze_products —
# junto a los 5 criterios de evaluación, tamaño real, zoom y las 3
# composiciones fijas. La IA la seguía mal: entre tantas otras consignas
# compitiendo por atención, la del usuario se diluía. Acá va SOLA, sin
# ningún otro criterio estético de por medio — es la ÚNICA instrucción que
# recibe la IA en esta llamada.
MAX_CUSTOM_PROMPT_LEN = 200  # una instrucción de dirección de arte no necesita más que esto


def _build_custom_layout_prompt(images_or_names: list, custom_prompt: str) -> str:
    names, product_brief = _product_brief(images_or_names)
    return f"""Eres un director de arte de fotografía de producto. Te muestro {len(names)} productos
(fotos reales) y el pedido de un cliente sobre cómo componerlos.

ESTE ES EL ÚNICO CRITERIO DE TODA LA TAREA — no hay otra consigna estética, no hay estilos
predefinidos que seguir. Cumple el pedido con la mayor fidelidad literal posible. El límite
obligatorio es que, si hay más de un producto, deben verse como una composición coherente:
jerarquía clara, centros separados, productos reconocibles y una relación visual entre ellos.
Ningún producto puede quedar totalmente tapado por otro o cortado fuera del lienzo. Más allá
de eso, prioriza el pedido
por sobre cualquier otro criterio de composición que se te ocurra.

PEDIDO DEL CLIENTE: "{custom_prompt}"

Productos:
{product_brief}

Además, para que los productos se vean en proporción entre sí como en un bodegón real, estima
el tamaño físico real de cada uno (no el tamaño del recorte de la foto) — un valor entre 0.3 y
1.0, donde 1.0 es el producto más grande del conjunto.

Para cada producto, sobre un lienzo cuadrado imaginario de 0.0 a 1.0 en cada eje (0,0 =
arriba-izquierda, 1,1 = abajo-derecha), dame el CENTRO (cx, cy — entre 0.12 y 0.88 en ambos
ejes) y la rotación en grados (-35 a 35; usa 0 salvo que el pedido mencione ángulos o
inclinación). Si hay 2 o 3 productos, separa sus centros al menos 0.20; si hay más, al menos
0.14 salvo solapamientos leves e intencionales.

Nombres exactos de los productos: {json.dumps(names)}

Responde ÚNICAMENTE con este JSON (sin texto adicional, sin markdown):
{{
  "label": "nombre corto (2-4 palabras) que resuma esta composición",
  "sizes": {{"NOMBRE_EXACTO": tamaño_relativo_0.3_a_1.0, ...}} para los {len(names)} productos,
  "layout": {{"NOMBRE_EXACTO": {{"cx":0.0_a_1.0,"cy":0.0_a_1.0,"rotation":-35_a_35}}, ...}} para los {len(names)} productos
}}"""


def _parse_custom_result(raw: str, names: list) -> dict:
    """Igual que _parse_result pero para el contrato más chico de la
    composición a medida — sin order/hero/detail_crop/free_layout_options.
    Reusa _normalize_sizes/_normalize_free_layout: mismo criterio de
    clamping que ya usa el resto de la app."""
    raw = (raw or "").strip()
    raw = re.sub(r"^```(?:json)?", "", raw).strip()
    raw = re.sub(r"```$", "", raw).strip()
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        raise ValueError(
            "La IA devolvió una respuesta incompleta o vacía "
            "(probable límite de tokens). Probá con una descripción más corta."
        )
    result = json.loads(m.group(0))
    label = str(result.get("label") or "A medida").strip()[:40] or "A medida"
    return {
        "label": label,
        "sizes": _normalize_sizes(result.get("sizes", {}), names),
        "layout": _normalize_free_layout(result.get("layout"), names),
    }


def _analyze_custom_gemini(images: list, api_key: str, custom_prompt: str) -> dict:
    try:
        from google import genai
        from google.genai import types
    except ImportError as e:
        raise RuntimeError("Falta el SDK de Gemini. Ejecuta: pip install google-genai") from e

    names = [img["name"] for img in images]
    client = genai.Client(api_key=api_key)
    contents = []
    for item in images:
        contents.append(f"[{item['name']}]")
        fp = item.get("filepath", "")
        if fp and Path(fp).exists():
            contents.append(types.Part.from_bytes(data=_to_jpeg_bytes(fp), mime_type="image/jpeg"))
    contents.append(_build_custom_layout_prompt(images, custom_prompt))

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.5,
            max_output_tokens=_dynamic_max_tokens(len(names), base=400, per_item=90, cap=4000),
        ),
    )
    return _parse_custom_result(response.text, names)


def _analyze_custom_claude(images: list, api_key: str, custom_prompt: str) -> dict:
    import anthropic

    names = [img["name"] for img in images]
    client = anthropic.Anthropic(api_key=api_key)
    content = []
    for item in images:
        content.append({"type": "text", "text": f"[{item['name']}]"})
        fp = item.get("filepath", "")
        if fp and Path(fp).exists():
            b64 = base64.standard_b64encode(_to_jpeg_bytes(fp)).decode()
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
            })
    content.append({"type": "text", "text": _build_custom_layout_prompt(images, custom_prompt)})

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=_dynamic_max_tokens(len(names), base=400, per_item=90, cap=4000),
        messages=[{"role": "user", "content": content}],
    )
    return _parse_custom_result(response.content[0].text, names)


def _analyze_custom_groq(images: list, api_key: str, custom_prompt: str) -> dict:
    try:
        from groq import Groq
    except ImportError as e:
        raise RuntimeError("Falta el SDK de Groq. Ejecuta: pip install groq") from e

    names = [img["name"] for img in images]
    client = Groq(api_key=api_key)
    content = []
    sent = 0
    for item in images:
        content.append({"type": "text", "text": f"[{item['name']}]"})
        fp = item.get("filepath", "")
        if fp and Path(fp).exists() and sent < GROQ_MAX_IMAGES:
            b64 = base64.standard_b64encode(_to_jpeg_bytes(fp)).decode()
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            })
            sent += 1
    content.append({"type": "text", "text": _build_custom_layout_prompt(images, custom_prompt)})

    response = client.chat.completions.create(
        model="qwen/qwen3.6-27b",
        messages=[{"role": "user", "content": content}],
        temperature=0.5,
        max_completion_tokens=_dynamic_max_tokens(len(names), base=200, per_item=50, cap=1200),
        reasoning_effort="none",
        response_format={"type": "json_object"},
    )
    return _parse_custom_result(response.choices[0].message.content, names)


def _analyze_custom_openai(images: list, api_key: str, custom_prompt: str) -> dict:
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError("Falta el SDK de OpenAI. Ejecuta: pip install openai") from e

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
    content.append({"type": "text", "text": _build_custom_layout_prompt(images, custom_prompt)})

    response = client.chat.completions.create(
        model="gpt-5.1",
        messages=[{"role": "user", "content": content}],
        temperature=0.5,
        max_completion_tokens=_dynamic_max_tokens(len(names), base=400, per_item=90, cap=4000),
        response_format={"type": "json_object"},
    )
    return _parse_custom_result(response.choices[0].message.content, names)


def analyze_custom_layout(
    images: list,
    custom_prompt: str,
    groq_key: str = None,
    gemini_key: str = None,
    anthropic_key: str = None,
    openai_key: str = None,
    provider: str = None,
) -> dict:
    """Composición dirigida ÚNICA Y EXCLUSIVAMENTE por custom_prompt — llamada
    aislada, no reutiliza ni compite con el análisis de analyze_products()
    (order/hero/sizes/detail_crop/3 estilos fijos). Returns: {label, sizes, layout}."""
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow no instalado")
    custom_prompt = (custom_prompt or "").strip()[:MAX_CUSTOM_PROMPT_LEN]
    if not custom_prompt:
        raise RuntimeError("Escribí una descripción antes de generar")

    images = _available_images(images)
    if not images:
        raise RuntimeError("No hay imágenes de productos accesibles para analizar")

    if not provider:
        provider = ("groq" if groq_key else
                    "gemini" if gemini_key else
                    "claude" if anthropic_key else "openai")

    if provider == "groq":
        if not groq_key:
            raise RuntimeError("GROQ_API_KEY no configurada")
        return _analyze_custom_groq(images, groq_key, custom_prompt)

    if provider == "gemini":
        if not gemini_key:
            raise RuntimeError("GEMINI_API_KEY no configurada")
        return _analyze_custom_gemini(images, gemini_key, custom_prompt)

    if provider == "claude":
        if not anthropic_key:
            raise RuntimeError("ANTHROPIC_API_KEY no configurada")
        return _analyze_custom_claude(images, anthropic_key, custom_prompt)

    if provider == "openai":
        if not openai_key:
            raise RuntimeError("OPENAI_API_KEY no configurada")
        return _analyze_custom_openai(images, openai_key, custom_prompt)

    raise RuntimeError(f"Proveedor de IA desconocido: {provider}")
