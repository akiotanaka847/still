"""
Renderer PNG para layouts de bodegón usando Pillow.
"""
from pathlib import Path

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


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

    # Productos
    for item in layout["items"]:
        fp = item.get("filepath", "")
        if not fp or not Path(fp).exists():
            continue
        try:
            img = Image.open(fp).convert("RGBA")
            img = img.resize((item["sw"], item["sh"]), Image.LANCZOS)
            canvas.paste(img, (item["x"], item["y"]), img)
        except Exception as e:
            print(f"Advertencia: no se pudo renderizar {Path(fp).name}: {e}")

    canvas.save(output_path, "PNG", optimize=True)


def get_image_dimensions(filepath: str) -> tuple:
    """Retorna (width, height) en píxeles de una imagen."""
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow no instalado")
    with Image.open(filepath) as img:
        return img.size
