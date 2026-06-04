"""
Módulo de propuesta de layout con IA.
Analiza los productos (visión) y sugiere el orden óptimo para el bodegón.

Proveedores soportados:
  - Gemini  (google-genai)   → modelo gemini-2.5-flash
  - Claude  (anthropic)      → modelo claude-sonnet-4-6

Ambos devuelven el MISMO contrato: {order: [...], hero: str, reasoning: str}.
El fondo sigue siendo transparente: la IA solo decide el ORDEN, el motor
geométrico (layout.py) compone igual que siempre.
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

Nombres exactos de los productos: {json.dumps(names)}

Responde ÚNICAMENTE con este JSON (sin texto adicional, sin markdown):
{{
  "order": [lista con los {len(names)} nombres exactos en el orden sugerido],
  "hero": "nombre del producto más prominente",
  "sizes": {{"NOMBRE_EXACTO": tamaño_relativo_0.3_a_1.0, ...}} para los {len(names)} productos,
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
    return result


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
            max_output_tokens=4096,
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
        max_tokens=4096,
        messages=[{"role": "user", "content": content}],
    )
    return _parse_result(response.content[0].text, names)


# ─── Backend: Groq (Llama 4 Scout, visión) ────────────────────────────────────
# Groq limita la visión de Llama 4 a ~5 imágenes por petición.
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
        model="meta-llama/llama-4-scout-17b-16e-instruct",
        messages=[{"role": "user", "content": content}],
        temperature=0.4,
        max_completion_tokens=4096,
        response_format={"type": "json_object"},
    )
    return _parse_result(response.choices[0].message.content, names)


# ─── Dispatcher ───────────────────────────────────────────────────────────────
def analyze_products(
    images: list,
    groq_key: str = None,
    gemini_key: str = None,
    anthropic_key: str = None,
    provider: str = None,
) -> dict:
    """
    Envía los thumbnails de los productos a la IA y obtiene el orden óptimo.

    provider: "groq" | "gemini" | "claude" | None
              (auto: groq → gemini → claude, según la primera key disponible).
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
                    "gemini" if gemini_key else "claude")

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

    raise RuntimeError(f"Proveedor de IA desconocido: {provider}")
