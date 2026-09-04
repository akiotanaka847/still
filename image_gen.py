"""Generación de bodegón COMPLETO con IA generativa (OpenAI gpt-image-2).

Modo alternativo y experimental al pipeline determinista (layout.py +
renderer.py): acá la IA redibuja la imagen final entera a partir de las
fotos de referencia (images.edit), en vez de solo proponer orden/tamaño/
posición para que Pillow los componga sin tocar los píxeles del producto.

Validado empíricamente (2026-08-30) con productos 100% inventados (texto
nunca visto por el modelo, para descartar que "recuerde" una marca real):
preserva el texto/branding de cada etiqueta con fidelidad perfecta, incluso
componiendo varios productos distintos en una sola escena, con fondo
transparente real. También probado con 14 productos reales de un catálogo
real (bebidas, snacks, dips, formas muy variadas) — buena fidelidad y
composición coherente, sin recorte de productos ni caos visual por la
cantidad. Ver memoria del proyecto para el detalle de ambas pruebas.

Solo OpenAI tiene este modelo — a diferencia de ai_proposal.py, no hay
fallback a Groq/Gemini/Claude acá.
"""

import base64
import io

from PIL import Image

from renderer import _content_bbox

# Tope propio, NO de la API: `images.edit` acepta 60 archivos sin quejarse.
# El limite es de CALIDAD, y se midio generando con productos reales del
# catalogo CON LOS AJUSTES DE ESTA FUNCION (1536x1024, quality="high"), que es
# lo unico que vale — ver tests/bodegon/README.md:
#
#   n=14  excelente. Sellos "EXCESO CALORIAS/AZUCARES" mexicanos correctos.
#   n=20  excelente. "Havana Club 7 ANOS", "TRES GENERACIONES ANEJO CRISTALINO".
#   n=30  muy bueno. Un solo error de marca visible.
#   n=45  bueno. "Ron ABUELO ANEJO RESERVA ESPECIAL 33% GRATIS".
#   n=52  bueno. "TEQUILA HACIENDA & TEPA RESERVA CRISTALINO", "Proximo by
#         MARQUES DE RISCAL RIOJA", "Jim Beam Kentucky Straight BOURBON",
#         "OREO with Coca-Cola POPPING CANDY". <- tope actual
#   n=60  COLAPSO, REPRODUCIDO 2 DE 2: el modelo descarta el pedido entero y
#         devuelve una imagen de stock ajena, sin un solo producto ni
#         transparencia (1a muestra: fiesta de cumpleanos familiar; 2a: una
#         persona en una oficina). NO es degradacion gradual: es abandono.
#
# El acantilado esta entre 53 y 59, sin acotar mas fino. 52 es el maximo
# VERIFICADO bueno.
#
# CUIDADO al medir con calidad baja: a 1024x1024 quality="low" el n=30 parecia
# inservible ("Modelox", "Bobelia", "ALMENDDA") y por eso el tope habia quedado
# en 20, menos de la mitad de lo posible. Medir SIEMPRE con --alta.
#
# Para lotes mayores no hay que subir este numero: hay que agrupar y generar
# por grupo (ver ROADMAP_PLATAFORMA.md, "grupos por CSV").
MAX_PRODUCTS_FOR_GENERATION = 52
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
Do NOT add any decorative elements that are not part of the actual products —
no fruit, ice cubes, water droplets, condensation, splashes, garnishes, or any
other invented prop or ingredient. Show ONLY the products themselves, well
arranged and lit, nothing added — UNLESS the client's instruction below
explicitly asks for such elements, in which case follow it.
Transparent background — this is MANDATORY and overrides any conflicting
request: no solid color, scene, or backdrop of any kind behind the products,
even if asked for below. Studio-quality product photography, high detail,
sharp focus."""
    if custom_prompt:
        prompt += f'\n\nInstrucción del cliente sobre estilo/composición (seguila con prioridad — incluí elementos decorativos SOLO si los pide explícitamente acá; sin perder la fidelidad del texto de las etiquetas): "{custom_prompt}"'
    else:
        prompt += "\n\nEl cliente no dio ninguna instrucción de estilo: limitate a organizar los productos de la forma visualmente más atractiva posible, sin agregar nada que no sea parte de los productos reales."
    return prompt


def generate_bodegon_image(images: list, api_key: str, custom_prompt: str = "", size: str = "1536x1024") -> bytes:
    """images: lista de dicts con al menos 'name' y 'filepath' (mismo shape que
    usa ai_proposal.analyze_products). Devuelve los bytes del PNG generado."""
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError("OpenAI SDK missing. Run: pip install openai") from e

    if len(images) > MAX_PRODUCTS_FOR_GENERATION:
        raise RuntimeError(
            f"This mode is experimental — try up to {MAX_PRODUCTS_FOR_GENERATION} "
            f"products for now (you have {len(images)})."
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

    png_bytes = base64.b64decode(result.data[0].b64_json)

    # gpt-image-2 devuelve el lienzo completo pedido (ej. 1536x1024) con los
    # productos centrados y bastante margen vacío alrededor — mismo problema
    # que ya se resolvió para el pipeline determinista (ver _content_bbox en
    # renderer.py): recortar DESPUÉS al contenido real, no intentar pedirle
    # el encuadre exacto al prompt.
    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    img = img.crop(_content_bbox(img))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def wrap_as_psd(png_bytes: bytes, layer_name: str = "AI composition") -> bytes:
    """Envuelve el PNG de Generación IA como PSD de UNA sola capa. A
    diferencia de renderer.render_psd (una capa por producto), acá no hay
    productos separados que envolver — la IA redibujó la escena entera
    como una sola imagen, así que el PSD solo aporta el formato/metadata
    para seguir editando en Photoshop, no capas por producto.

    Reusa el mismo workaround que renderer.render_psd: el auto-flatten
    interno de psd-tools al guardar un documento recién creado está roto
    (ver ese docstring para el detalle) — se inyecta el preview a mano."""
    try:
        from psd_tools import PSDImage
    except ImportError as e:
        raise RuntimeError("Falta psd-tools. Ejecuta: pip install psd-tools") from e

    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    psd = PSDImage.new("RGBA", img.size)
    psd.create_pixel_layer(img, name=layer_name, top=0, left=0)

    channels_bytes = [c.tobytes() for c in img.split()]
    psd._record.image_data.set_data(channels_bytes, psd._record.header)
    psd._updated = False

    buf = io.BytesIO()
    psd.save(buf)
    return buf.getvalue()
