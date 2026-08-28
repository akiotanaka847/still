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
distintivo, un ícono) — NO un área lisa/vacía sin nada que mostrar. Da su ubicación como
un recuadro en fracciones de 0.0 a 1.0 relativas SOLO al recuadro del héroe (x,y = esquina
superior izquierda del recuadro; w,h = ancho/alto), pensado para una burbuja circular de
zoom de ~30% de ancho x 20% de alto del producto.

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
Para cada una, y para cada producto, sobre un lienzo cuadrado imaginario de 0.0 a 1.0 en cada
eje (0,0 = arriba-izquierda, 1,1 = abajo-derecha), dame:
  - cx, cy: el CENTRO del producto. El héroe suele ir más al centro y grande; los secundarios
    pueden ir más chicos, superpuestos, a los costados o a distinta altura — pero mantené todos
    entre 0.12 y 0.88 en ambos ejes para que ninguno quede cortado contra el borde. IMPORTANTE:
    con 2 productos, sus cx NUNCA deben quedar a menos de ~0.2 de distancia entre sí (p. ej. 0.35
    y 0.65, no 0.48 y 0.52) — si quedan casi en el mismo punto, uno tapa por completo al otro y
    se pierde el producto. En "Elegante" en particular, separalos con criterio generoso: se busca
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
    result.setdefault("reasoning", "")
    result["sizes"] = _normalize_sizes(result.get("sizes", {}), names)
    result["detail_crop"] = _normalize_detail_crop(result.get("detail_crop"))
    result["free_layout_options"] = _normalize_free_layout_options(result.get("free_layout_options"), names)
    return result


def _normalize_free_layout_options(options, names):
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
        out.append({"label": label, "layout": _normalize_free_layout(opt.get("layout"), names)})
    return out


def _normalize_free_layout(free_layout, names):
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
            cx = max(0.08, min(0.92, float(pos.get("cx", 0.5))))
            cy = max(0.08, min(0.92, float(pos.get("cy", 0.5))))
            rot = max(-35.0, min(35.0, float(pos.get("rotation", 0.0))))
        except (TypeError, ValueError):
            cx, cy, rot = 0.5, 0.5, 0.0
        out[n] = {"cx": round(cx, 3), "cy": round(cy, 3), "rotation": round(rot, 1)}
    return out


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
    contents.append(_build_prompt(names))

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
    content.append({"type": "text", "text": _build_prompt(names)})

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
    content.append({"type": "text", "text": _build_prompt(names)})

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
    content.append({"type": "text", "text": _build_prompt(names)})

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
