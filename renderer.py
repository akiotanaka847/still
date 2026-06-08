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


def _draw_contact_shadow(canvas, item, opacity, blur):
    """Dibuja una sombra de contacto elíptica y difuminada bajo el producto."""
    sw, sh = item["sw"], item["sh"]
    x, y = item["x"], item["y"]
    base_y = y + sh

    ell_w = max(4, int(sw * 0.92))
    ell_h = max(6, int(sh * 0.11))
    pad = max(1, blur * 3)

    layer = Image.new("RGBA", (ell_w + pad * 2, ell_h + pad * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    alpha = max(0, min(255, int(255 * opacity)))
    draw.ellipse([pad, pad, pad + ell_w, pad + ell_h], fill=(0, 0, 0, alpha))
    layer = layer.filter(ImageFilter.GaussianBlur(blur))

    cx = x + sw // 2
    px = cx - (ell_w // 2 + pad)
    # La sombra se asienta en la base, ligeramente solapada hacia arriba
    py = base_y - (ell_h // 2 + pad) - int(ell_h * 0.10)
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

    # Sombras primero (en el mismo orden), para que ningún producto las tape
    if shadow:
        for item in items:
            fp = item.get("filepath", "")
            if not fp or not Path(fp).exists():
                continue
            try:
                _draw_contact_shadow(canvas, item, shadow_opacity, shadow_blur)
            except Exception as e:
                print(f"Advertencia: sombra fallida en {Path(fp).name}: {e}")

    # Productos — recortados a su contenido real
    for item in items:
        fp = item.get("filepath", "")
        if not fp or not Path(fp).exists():
            continue
        try:
            img = _load_trimmed(fp)
            img = img.resize((item["sw"], item["sh"]), Image.LANCZOS)
            canvas.alpha_composite(img, (item["x"], item["y"]))
        except Exception as e:
            print(f"Advertencia: no se pudo renderizar {Path(fp).name}: {e}")

    canvas.save(output_path, "PNG", optimize=True)


def get_image_dimensions(filepath: str) -> tuple:
    """Retorna (width, height) del contenido real (recortado) de una imagen."""
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow no instalado")
    with Image.open(filepath) as img:
        img = img.convert("RGBA")
        l, t, r, b = _content_bbox(img)
        return (r - l, b - t)
