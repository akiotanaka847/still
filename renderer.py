"""
Renderer PNG para layouts de bodegón usando Pillow.
"""
from pathlib import Path

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


# Tolerancia por defecto para detectar el borde del producto.
#   alpha_threshold: píxeles con alpha <= esto se consideran "vacío" (0-255)
#   color_tolerance: diferencia de color <= esto respecto al fondo se considera "vacío" (0-255)
DEFAULT_ALPHA_THRESHOLD = 10
DEFAULT_COLOR_TOLERANCE = 12


def _content_bbox(img, alpha_threshold=DEFAULT_ALPHA_THRESHOLD,
                  color_tolerance=DEFAULT_COLOR_TOLERANCE):
    """
    Bounding box del contenido real del producto, ignorando los márgenes
    vacíos (transparentes o de color uniforme) alrededor.

    alpha_threshold: tolerancia para halos semitransparentes (anti-aliasing).
    color_tolerance: tolerancia para fondos casi-uniformes (degradados suaves, JPEG).
    """
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    full = (0, 0, img.width, img.height)

    # 1) Recorte por canal alpha (productos con fondo transparente)
    alpha = img.split()[-1]
    if alpha_threshold > 0:
        # Binariza: todo lo que supere el umbral cuenta como contenido
        mask = alpha.point(lambda a: 255 if a > alpha_threshold else 0)
        bbox = mask.getbbox()
    else:
        bbox = alpha.getbbox()

    # 2) Si la imagen es totalmente opaca, recortar por color de fondo.
    #    Se promedia el color de las 4 esquinas para ser robusto a ruido.
    if bbox is None or bbox == full:
        rgb = img.convert("RGB")
        w, h = rgb.size
        corners = [
            rgb.getpixel((0, 0)),
            rgb.getpixel((w - 1, 0)),
            rgb.getpixel((0, h - 1)),
            rgb.getpixel((w - 1, h - 1)),
        ]
        bg_color = tuple(sum(c[i] for c in corners) // 4 for i in range(3))
        bg = Image.new("RGB", rgb.size, bg_color)
        diff = ImageChops.difference(rgb, bg).convert("L")
        if color_tolerance > 0:
            diff = diff.point(lambda d: 255 if d > color_tolerance else 0)
        bbox2 = diff.getbbox()
        if bbox2:
            bbox = bbox2

    return bbox or full


def _load_trimmed(filepath: str):
    """Abre la imagen y la recorta a su contenido real. Retorna la imagen RGBA recortada.

    NOTA: tras F1 las entradas ya están pre-normalizadas por normalize.py (packshot
    transparente y con bbox ajustado), por lo que ``_content_bbox`` solo hace
    ``alpha.getbbox()`` (casi un no-op) y la rama de color-de-esquina queda como
    defensa-en-profundidad para entradas legacy/no normalizadas.
    """
    img = Image.open(filepath).convert("RGBA")
    return img.crop(_content_bbox(img))


def _draw_silhouette_shadow(canvas, item, product_img, opacity, blur):
    """
    Sombra de contacto REAL basada en la silueta del producto (no una elipse genérica).

    Toma el canal alfa del producto, lo aplasta verticalmente al plano del piso
    (proyección de contacto), le aplica un degradado de opacidad (más oscuro donde el
    producto toca el piso, desvaneciéndose hacia afuera) y lo difumina. El resultado
    respeta la forma real del producto — calidad de fotografía, no un manchón ovalado.
    """
    sw, sh = item["sw"], item["sh"]
    x, y = item["x"], item["y"]
    base_y = y + sh

    # Silueta del producto (ya viene a tamaño sw×sh).
    alpha = product_img.split()[-1]

    # Aplastar al plano del piso: la sombra es una franja baja (~16% de la altura).
    shadow_h = max(6, int(sh * 0.16))
    squashed = alpha.resize((max(1, sw), shadow_h), Image.LANCZOS)

    # Degradado vertical: 1.0 en el contacto (arriba) → 0 abajo.
    grad = Image.new("L", (1, shadow_h))
    for i in range(shadow_h):
        grad.putpixel((0, i), int(255 * (1.0 - i / max(1, shadow_h - 1))))
    grad = grad.resize((max(1, sw), shadow_h))

    # Silueta × degradado × opacidad → alfa de la sombra.
    shadow_alpha = ImageChops.multiply(squashed, grad)
    op = max(0.0, min(1.0, opacity))
    shadow_alpha = shadow_alpha.point(lambda a: int(a * op))

    # Capa negra con ese alfa, con padding para que el blur no se recorte.
    pad = max(2, int(blur * 2))
    layer = Image.new("RGBA", (sw + pad * 2, shadow_h + pad * 2), (0, 0, 0, 0))
    black = Image.new("RGBA", (sw, shadow_h), (0, 0, 0, 255))
    layer.paste(black, (pad, pad), shadow_alpha)
    layer = layer.filter(ImageFilter.GaussianBlur(blur))

    # Asentar en la base, ligeramente solapada hacia arriba para el contacto.
    px = x - pad
    py = base_y - int(shadow_h * 0.35) - pad
    canvas.alpha_composite(layer, (px, py))


def render_png(layout: dict, output_path: str, background_path: str = None) -> None:
    """
    Composita las imágenes del layout en un canvas y guarda como PNG.

    layout: resultado de layout.compute_layout()
    output_path: ruta de salida .png
    background_path: imagen de fondo opcional (se escala al canvas completo)
    """
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow no instalado. Ejecuta: pip install Pillow")

    cw = layout["canvas_width"]
    ch = layout["canvas_height"]

    canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))

    # Fondo/plantilla
    if background_path and Path(background_path).exists():
        try:
            bg = Image.open(background_path).convert("RGBA")
            bg = bg.resize((cw, ch), Image.LANCZOS)
            canvas.paste(bg, (0, 0), bg)
        except Exception as e:
            print(f"Advertencia: no se pudo cargar el fondo: {e}")

    effects = layout.get("effects", {})
    shadow = bool(effects.get("shadow"))
    shadow_opacity = float(effects.get("shadow_opacity", 0.30))
    shadow_blur = int(effects.get("shadow_blur", 12))

    # Ordenar por z para respetar la profundidad (lo de mayor z se pinta encima)
    items = sorted(layout["items"], key=lambda it: it.get("z", 0))

    # Precargar cada producto UNA vez (recortado + escalado), alineado a su item.
    # Se reutiliza tanto para la sombra de silueta como para el composite final.
    loaded = []  # (item, img)
    for item in items:
        fp = item.get("filepath", "")
        if not fp or not Path(fp).exists():
            continue
        try:
            img = _load_trimmed(fp).resize((item["sw"], item["sh"]), Image.LANCZOS)
            loaded.append((item, img))
        except Exception as e:
            print(f"Advertencia: no se pudo cargar {Path(fp).name}: {e}")

    # Sombras primero (basadas en la silueta real), para que ningún producto las tape.
    if shadow:
        for item, img in loaded:
            try:
                _draw_silhouette_shadow(canvas, item, img, shadow_opacity, shadow_blur)
            except Exception as e:
                print(f"Advertencia: sombra fallida en {item.get('name', '?')}: {e}")

    # Productos, de atrás hacia adelante (loaded ya viene ordenado por z).
    # Antes de pintar cada producto (salvo el del fondo), proyecta su SOMBRA DE
    # OCLUSIÓN sobre lo ya pintado: así el producto de adelante ensombrece al de
    # atrás donde se solapan → lee como profundidad real, no como recortes pegados.
    # La sombra se enmascara con el alfa actual del canvas, por lo que es invisible
    # cuando los productos NO se solapan (estilos sin solapamiento quedan limpios).
    for idx, (item, img) in enumerate(loaded):
        if idx > 0:
            try:
                _draw_occlusion_shadow(canvas, item, img)
            except Exception as e:
                print(f"Advertencia: oclusión fallida en {item.get('name', '?')}: {e}")
        canvas.alpha_composite(img, (item["x"], item["y"]))

    canvas.save(output_path, "PNG", optimize=True)


def _draw_occlusion_shadow(canvas, item, product_img, opacity=0.32):
    """
    Sombra que el producto de ADELANTE proyecta sobre lo que ya está pintado detrás,
    SOLO donde se solapan (se enmascara con el alfa actual del canvas). Esto da la
    separación de profundidad que evita el look de "recortes pegados". Si no hay
    solapamiento, la máscara queda vacía y no se dibuja nada.
    """
    cw, ch = canvas.size
    sw, sh = item["sw"], item["sh"]
    x, y = item["x"], item["y"]
    blur = max(4, int(sw * 0.05))
    # Desplazamiento sutil (el producto "levanta" del plano de atrás).
    dx, dy = int(sw * 0.05), int(sh * 0.03)

    sil = product_img.split()[-1]
    occ = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    black = Image.new("RGBA", (sw, sh), (0, 0, 0, int(255 * opacity)))
    occ.paste(black, (x + dx, y + dy), sil)        # silueta negra desplazada
    occ = occ.filter(ImageFilter.GaussianBlur(blur))
    # Solo donde ya hay contenido pintado detrás (intersección con el alfa del canvas).
    masked = ImageChops.multiply(occ.split()[-1], canvas.split()[-1])
    occ.putalpha(masked)
    canvas.alpha_composite(occ)


def get_image_dimensions(filepath: str) -> tuple:
    """Retorna (width, height) del contenido real (recortado) de una imagen."""
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow no instalado")
    with Image.open(filepath) as img:
        img = img.convert("RGBA")
        l, t, r, b = _content_bbox(img)
        return (r - l, b - t)
