"""
Módulo de propuesta de layout con IA.
Usa Claude Vision para analizar los productos y sugerir el orden óptimo.
"""
import base64
import io
import json
import re
from pathlib import Path

import anthropic

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


def _to_jpeg_b64(filepath: str, max_size: int = 512) -> str:
    """Redimensiona una imagen y devuelve base64 JPEG para la API."""
    with Image.open(filepath) as img:
        img.thumbnail((max_size, max_size), Image.LANCZOS)

        # Convertir a RGB (la API no acepta RGBA/P)
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
        return base64.standard_b64encode(buf.getvalue()).decode()


def analyze_products(images: list, api_key: str) -> dict:
    """
    Envía los thumbnails de los productos a Claude y obtiene el orden óptimo.

    images: lista de {name, filepath, width, height}
    Returns: {order: [nombres], hero: nombre, reasoning: str}
    """
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow no instalado")

    client = anthropic.Anthropic(api_key=api_key)
    names = [img["name"] for img in images]
    content = []

    for item in images:
        fp = item.get("filepath", "")
        if not fp or not Path(fp).exists():
            content.append({"type": "text", "text": f"[{item['name']}] — imagen no disponible"})
            continue
        try:
            b64 = _to_jpeg_b64(fp)
            content.append({"type": "text", "text": f"[{item['name']}]"})
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
            })
        except Exception as e:
            content.append({"type": "text", "text": f"[{item['name']}] — error: {e}"})

    content.append({
        "type": "text",
        "text": f"""Eres un experto en diseño visual de bodegones comerciales y fotografía de producto.

Analiza los {len(images)} productos mostrados y determina el orden óptimo para componer un bodegón visualmente atractivo.

Criterios de evaluación:
1. Producto "héroe": el más llamativo o icónico va en posición prominente (centro o primer plano)
2. Equilibrio de color: evita agrupar tonos similares, busca contraste y armonía
3. Contraste de formas: alterna productos verticales y horizontales para crear ritmo
4. Peso visual: distribuye productos de apariencia "pesada" (oscuros, grandes) con los más "ligeros"
5. Flujo natural: el ojo del espectador debe moverse fluidamente por la composición

Nombres exactos de los productos: {json.dumps(names)}

Responde ÚNICAMENTE con este JSON (sin texto adicional, sin markdown):
{{
  "order": [lista con los {len(names)} nombres exactos en el orden sugerido],
  "hero": "nombre del producto más prominente",
  "reasoning": "explicación de 1-2 oraciones en español de por qué este orden"
}}""",
    })

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        messages=[{"role": "user", "content": content}],
    )

    raw = response.content[0].text.strip()

    # Extraer JSON si viene envuelto en markdown
    json_match = re.search(r"\{[\s\S]*\}", raw)
    if json_match:
        raw = json_match.group(0)

    result = json.loads(raw)

    # Validar y completar si faltan nombres
    valid = set(names)
    result["order"] = [n for n in result.get("order", []) if n in valid]
    missing = [n for n in names if n not in set(result["order"])]
    result["order"].extend(missing)

    return result
