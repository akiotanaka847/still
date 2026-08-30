"""Generación de bodegón COMPLETO con IA generativa (OpenAI gpt-image-2).

Modo alternativo y experimental al pipeline determinista (layout.py +
renderer.py): acá la IA redibuja la imagen final entera a partir de las
fotos de referencia (images.edit), en vez de solo proponer orden/tamaño/
posición para que Pillow los componga sin tocar los píxeles del producto.

Validado empíricamente (2026-08-30) con productos 100% inventados (texto
nunca visto por el modelo, para descartar que "recuerde" una marca real):
preserva el texto/branding de cada etiqueta con fidelidad perfecta, incluso
componiendo varios productos distintos en una sola escena, con fondo
transparente real. Ver memoria del proyecto para el detalle de la prueba.

Solo OpenAI tiene este modelo — a diferencia de ai_proposal.py, no hay
fallback a Groq/Gemini/Claude acá.
"""

import base64

MAX_PRODUCTS_FOR_GENERATION = 12
MAX_CUSTOM_PROMPT_LEN = 300


def _build_generation_prompt(names: list, custom_prompt: str) -> str:
    productos = ", ".join(names)
    prompt = f"""Create a professional product photography composition (bodegón)
featuring ALL of these {len(names)} products together in the same scene: {productos}.
Preserve each product's real shape, colors, and ALL text/branding on their labels
EXACTLY as shown in the reference images, character for character — do not alter,
invent, or omit any text on any label.
Arrange them together in a coherent, visually balanced composition (not just
side by side floating disconnected) with professional studio lighting, subtle
shadows, and a sense of depth between products.
Transparent background. Studio-quality product photography, high detail, sharp
focus."""
    if custom_prompt:
        prompt += f'\n\nInstrucción adicional del cliente sobre estilo/composición (seguila con prioridad, sin perder la fidelidad del texto de las etiquetas): "{custom_prompt}"'
    return prompt


def generate_bodegon_image(images: list, api_key: str, custom_prompt: str = "", size: str = "1536x1024") -> bytes:
    """images: lista de dicts con al menos 'name' y 'filepath' (mismo shape que
    usa ai_proposal.analyze_products). Devuelve los bytes del PNG generado."""
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError("Falta el SDK de OpenAI. Ejecuta: pip install openai") from e

    if len(images) > MAX_PRODUCTS_FOR_GENERATION:
        raise RuntimeError(
            f"Este modo es experimental — probalo con hasta {MAX_PRODUCTS_FOR_GENERATION} "
            f"productos por ahora (tenés {len(images)})."
        )

    names = [img["name"] for img in images]
    prompt = _build_generation_prompt(names, (custom_prompt or "").strip()[:MAX_CUSTOM_PROMPT_LEN])

    client = OpenAI(api_key=api_key)
    files = [open(img["filepath"], "rb") for img in images]
    try:
        result = client.images.edit(
            model="gpt-image-2",
            image=files,
            prompt=prompt,
            size=size,
            quality="high",
            background="transparent",
        )
    finally:
        for f in files:
            f.close()

    return base64.b64decode(result.data[0].b64_json)
