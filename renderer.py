"""
Renderer PNG para layouts de bodegón usando Pillow.
"""
import math
from pathlib import Path

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageEnhance, ImageOps
    PIL_AVAILABLE = True
    # Fotografía de producto real (usuarios autenticados, no uploads anónimos)
    # puede venir en resoluciones muy altas — el límite por defecto de Pillow
    # (~89 MP, pensado para frenar "decompression bombs" de origen anónimo)
    # rechazaba en silencio algunas fotos de estudio válidas. Se sube a un
    # techo generoso en vez de sacarlo del todo, para no perder la protección.
    Image.MAX_IMAGE_PIXELS = 300_000_000
except ImportError:
    PIL_AVAILABLE = False


# ─── Galería de fondos de estudio (IA, pre-generados una sola vez y reusados) ──
BACKGROUNDS_DIR = Path("static/backgrounds")
STUDIO_BACKGROUND_STYLES = {
    "studio_dark": "studio_dark.png",
    "electric":    "electric.png",
    "splash":      "splash.png",
}

# ─── Resolución de exportación ─────────────────────────────────────────────
# El layout se calcula en unidades "de preview" (base_height chico, para que
# la vista previa/miniaturas sean instantáneas) — pero el PNG final no debe
# quedarse en esa resolución si el usuario subió fotos de mayor calidad.
MAX_EXPORT_SCALE = 2.0   # no upscalear más que esto aunque el original sea gigante
MAX_CANVAS_DIM = 4000    # tope duro del lado mayor del canvas final (peso/tiempo de render)


def _compute_export_scale(layout: dict) -> float:
    """Cuánto agrandar el layout completo para el render final, de modo que
    NINGÚN producto quede innecesariamente submuestreado respecto a su
    resolución ORIGINAL. Mira los {oh/sh} de TODOS los productos (no solo el
    de mayor tamaño asignado) — en estrategias sin escala de héroe (Fila
    recta, Inclinado ★, etc.) todos miden parecido en el layout aunque las
    fotos originales tengan resoluciones muy distintas, así que el que más
    "resolución sin usar" tiene puede no ser el más grande en pantalla.
    No toca la vista previa — layout.py sigue calculando posiciones/tamaños
    relativos con base_height chico; esto solo escala el resultado antes de
    componer el PNG final."""
    items = layout.get("items") or []
    ratios = [it["oh"] / it["sh"] for it in items
              if it.get("sh", 0) > 0 and it.get("oh", 0) > 0]
    max_dim = max(layout.get("canvas_width", 0), layout.get("canvas_height", 0), 1)
    tope = MAX_CANVAS_DIM / max_dim          # <1 cuando hay que REDUCIR

    if not ratios:
        return min(1.0, tope)

    scale = max(1.0, min(MAX_EXPORT_SCALE, max(ratios)))
    # MAX_CANVAS_DIM decía ser "tope duro del lado mayor del canvas final",
    # pero el `max(1.0, ...)` del final lo volvía tope solo HACIA ARRIBA: con
    # un lienzo lógico ya mayor a 4000 px esto calculaba la reducción correcta
    # y acto seguido la descartaba, así que no había ningún techo real. Un
    # bodegón de 60 productos salía a 7616x4985 (38 MP) y uno de 30 a 19 MP.
    #
    # Ahora reduce de verdad, que es lo que hace que la pieza degrade con
    # gracia: al crecer la cantidad, el producto ocupa menos px en la salida
    # (60 productos → ~442 px de alto cada uno) en vez de que el archivo
    # crezca sin límite. Los productos son fotos de alta resolución, así que
    # reducir no pierde nitidez: pierde tamaño, que es lo esperado.
    return min(scale, tope) if max_dim * scale > MAX_CANVAS_DIM else scale


def _scale_layout(layout: dict, scale: float) -> dict:
    """Copia el layout con todas las medidas multiplicadas por 'scale' — la
    composición (posiciones relativas, ángulos, orden, qué tapa a qué) queda
    idéntica; solo cambia la resolución de salida."""
    if scale == 1.0:
        return layout
    out = dict(layout)
    out["canvas_width"] = round(layout["canvas_width"] * scale)
    out["canvas_height"] = round(layout["canvas_height"] * scale)
    out["items"] = [
        {**it,
         "x": round(it["x"] * scale), "y": round(it["y"] * scale),
         "sw": round(it["sw"] * scale), "sh": round(it["sh"] * scale)}
        for it in layout["items"]
    ]
    eff = dict(layout.get("effects") or {})
    if "shadow_blur" in eff:
        eff["shadow_blur"] = max(1, round(eff["shadow_blur"] * scale))
    if "shadow_margin" in eff:
        eff["shadow_margin"] = round(eff["shadow_margin"] * scale)
    out["effects"] = eff
    return out


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
    """Abre la imagen y la recorta a su contenido real. Retorna la imagen RGBA recortada."""
    img = Image.open(filepath).convert("RGBA")
    return img.crop(_content_bbox(img))


def _draw_contact_shadow(canvas, item, opacity, blur, size_scale=1.0):
    """Dibuja una sombra de contacto elíptica y difuminada bajo el producto.
    size_scale (<1) la achica y desvanece — usado por 'flotante' para que los
    productos que quedan más 'lejos' del piso implícito tengan una sombra
    más tenue, sugiriendo que flotan a distinta altura."""
    sw, sh = item["sw"], item["sh"]
    x, y = item["x"], item["y"]
    base_y = y + sh

    ell_w = max(4, int(sw * 0.92 * size_scale))
    ell_h = max(6, int(sh * 0.11 * size_scale))
    pad = max(1, blur * 3)

    layer = Image.new("RGBA", (ell_w + pad * 2, ell_h + pad * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    alpha = max(0, min(255, int(255 * opacity * size_scale)))
    draw.ellipse([pad, pad, pad + ell_w, pad + ell_h], fill=(0, 0, 0, alpha))
    layer = layer.filter(ImageFilter.GaussianBlur(blur))

    cx = x + sw // 2
    px = cx - (ell_w // 2 + pad)
    # La sombra se asienta en la base, ligeramente solapada hacia arriba
    py = base_y - (ell_h // 2 + pad) - int(ell_h * 0.10)
    canvas.alpha_composite(layer, (px, py))


def _studio_grade(img, contrast=1.14, color=1.12, brightness=1.03):
    """Ligero grading de contraste/saturación — hace que el producto se sienta
    'iluminado de estudio' en vez de un recorte plano. Solo se usa junto con
    un fondo de estudio (background_style); no toca las estrategias normales."""
    alpha = img.split()[-1]
    rgb = img.convert("RGB")
    rgb = ImageEnhance.Contrast(rgb).enhance(contrast)
    rgb = ImageEnhance.Color(rgb).enhance(color)
    rgb = ImageEnhance.Brightness(rgb).enhance(brightness)
    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    return out


def _studio_reflection(img, fade_frac=0.45, start_opacity=0.22):
    """Reflejo espejado con fade vertical (técnica clásica de product shots)."""
    refl = ImageOps.flip(img)
    w, h = refl.size
    fade_h = max(1, int(h * fade_frac))
    grad = Image.new("L", (1, h), 0)
    for y in range(fade_h):
        a = int(start_opacity * 255 * (1 - y / fade_h))
        grad.putpixel((0, y), a)
    alpha_mask = grad.resize((w, h))
    a = ImageChops.multiply(refl.split()[-1], alpha_mask)
    refl.putalpha(a)
    return refl


def _directional_mask(size, angle_deg):
    """Degradado lineal rotado: blanco del lado de la 'fuente de luz', negro
    del lado opuesto (vía Image.linear_gradient, sin loops por píxel)."""
    w, h = size
    diag = int(math.hypot(w, h)) + 4
    base = Image.linear_gradient("L").resize((diag, diag))
    rotated = base.rotate(angle_deg, resample=Image.BICUBIC)
    left, top = (diag - w) // 2, (diag - h) // 2
    return rotated.crop((left, top, left + w, top + h))


def _studio_rim_glow(img, tint=(255, 238, 210), grow=9, blur=9, opacity=0.6, angle_deg=200):
    """Aro de luz SOLO en el borde y de UN lado (como una fuente real de
    estudio) — no parejo en todo el contorno, que en productos claros se lee
    como un halo de sticker en vez de una luz de borde."""
    alpha = img.split()[-1]
    dilated = alpha.filter(ImageFilter.MaxFilter(grow * 2 + 1))
    ring = ImageChops.subtract(dilated, alpha)
    ring = ring.filter(ImageFilter.GaussianBlur(blur))
    ring = ImageChops.multiply(ring, _directional_mask(img.size, angle_deg))
    glow = Image.new("RGBA", img.size, tint + (0,))
    glow.putalpha(ring.point(lambda p: int(min(255, p) * opacity)))
    return glow


DEFAULT_ZOOM_REGION_SIZE = (0.30, 0.20)  # ancho, alto — como fracción del producto


def _auto_detail_region(img, size_frac=DEFAULT_ZOOM_REGION_SIZE):
    """Encuentra automáticamente una región con mucho detalle visual (texto,
    logos, íconos — bordes/contraste alto) en vez de recortar siempre la
    misma fracción fija, que a veces cae en una zona lisa sin nada que
    mostrar. FIND_EDGES + BoxBlur aproxima la 'energía' de cada ventana
    (el blur promedia los bordes alrededor de cada píxel); el pico de esa
    imagen difuminada es el centro de la ventana con más detalle."""
    w, h = img.size
    win_w = max(4, int(w * size_frac[0]))
    win_h = max(4, int(h * size_frac[1]))
    edges = img.convert("L").filter(ImageFilter.FIND_EDGES)
    energy = edges.filter(ImageFilter.BoxBlur(max(win_w, win_h) // 2))

    step = 4
    small = energy.resize((max(1, w // step), max(1, h // step)))
    sw_, sh_ = small.size
    pixels = list(small.getdata())
    idx = max(range(len(pixels)), key=lambda i: pixels[i])
    cx, cy = (idx % sw_) * step, (idx // sw_) * step

    x = max(0, min(w - win_w, cx - win_w // 2))
    y = max(0, min(h - win_h, cy - win_h // 2))
    return {"x": x / w, "y": y / h, "w": win_w / w, "h": win_h / h}


def _zoom_bubble(img, region, diameter, ring_color=(255, 255, 255, 255), ring_w=None):
    """Burbuja circular con un recorte ampliado de 'img' (región en fracciones
    0-1 de su propio tamaño). Aro + sombra suave, como una lupa de detalle.

    El recorte real SIEMPRE es cuadrado (misma área aprox. que la región
    dada, centrado en ella) — la región de entrada puede venir con cualquier
    proporción (p. ej. la IA a veces sugiere un recuadro ancho y bajo), y
    forzarla tal cual en un círculo se vería estirada/deformada."""
    w, h = img.size
    rx = max(0.0, min(0.95, region.get("x", 0.30)))
    ry = max(0.0, min(0.95, region.get("y", 0.30)))
    rw = max(0.03, min(1.0 - rx, region.get("w", 0.30)))
    rh = max(0.03, min(1.0 - ry, region.get("h", 0.20)))
    cx, cy = (rx + rw / 2) * w, (ry + rh / 2) * h
    side = max(24, min(w, h, ((rw * w) * (rh * h)) ** 0.5))
    l = int(max(0, min(w - side, cx - side / 2)))
    t = int(max(0, min(h - side, cy - side / 2)))
    r, b = int(min(w, l + side)), int(min(h, t + side))
    crop = img.crop((l, t, r, b)).convert("RGBA").resize((diameter, diameter), Image.LANCZOS)

    mask = Image.new("L", (diameter, diameter), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, diameter, diameter], fill=255)
    circle = Image.new("RGBA", (diameter, diameter), (0, 0, 0, 0))
    circle.paste(crop, (0, 0), mask)

    ring_w = ring_w or max(4, diameter // 32)
    pad = ring_w + 14
    out = Image.new("RGBA", (diameter + pad * 2, diameter + pad * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", out.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse(
        [pad - 4, pad + 6, pad + diameter + 4, pad + diameter + 14], fill=(0, 0, 0, 130))
    out.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(10)))
    ImageDraw.Draw(out).ellipse(
        [pad - ring_w, pad - ring_w, pad + diameter + ring_w, pad + diameter + ring_w], fill=ring_color)
    out.alpha_composite(circle, (pad, pad))
    return out, (l, t, r, b)


def _composite_zoom_detail(canvas, items, rendered, zoom_detail):
    """Ubica la burbuja de zoom sobre el producto 'target' (o el héroe —
    el más alto, mismo criterio que el resto de las estrategias — si no se
    especifica / no se encuentra), con una línea guía hacia el recorte."""
    target_name = (zoom_detail or {}).get("target")
    item = next((it for it in items if it["name"] == target_name), None) if target_name else None
    if item is None:
        item = max(items, key=lambda it: it["sh"])
    if item["name"] not in rendered:
        return

    img, px, py = rendered[item["name"]]
    # Prioridad: región explícita (típicamente sugerida por la IA de visión,
    # que sí "ve" dónde está el logo/detalle) → si no hay, se detecta la zona
    # con más detalle visual automáticamente (ver _auto_detail_region).
    region = (zoom_detail or {}).get("region") or _auto_detail_region(img)
    diameter = max(90, int(max(img.width, img.height) * 0.40))
    bubble, crop_box = _zoom_bubble(img, region, diameter)

    # Ancla junto al borde derecho, a la altura del centro del recorte —
    # solo ~28% de la burbuja se monta sobre el producto (el resto queda
    # afuera, para que no se lea como una copia pegada de la misma etiqueta)
    # y se clampea para que nunca quede cortada contra el lienzo.
    cx0 = px + (crop_box[0] + crop_box[2]) // 2
    cy0 = py + (crop_box[1] + crop_box[3]) // 2
    bx = px + img.width - int(bubble.width * 0.28)
    by = cy0 - bubble.height // 2
    bx = max(4, min(bx, canvas.width - bubble.width - 4))
    by = max(4, min(by, canvas.height - bubble.height - 4))

    draw = ImageDraw.Draw(canvas)
    draw.line([(cx0, cy0), (bx + bubble.width // 2, by + bubble.height // 2)],
              fill=(255, 255, 255, 160), width=3)
    canvas.alpha_composite(bubble, (bx, by))


def _process_item_image(item: dict):
    """Recorte + escala + rotación de UN producto — misma lógica que usa
    render_png, extraída para que render_psd (capas por producto) componga
    exactamente igual sin duplicar el criterio de rotación/anclaje.
    Retorna (img_rgba, px, py) en coordenadas del lienzo de referencia."""
    fp = item["filepath"]
    img = _load_trimmed(fp)
    img = img.resize((item["sw"], item["sh"]), Image.LANCZOS)
    rotation = item.get("rotation", 0.0)
    if rotation:
        cx = item["x"] + item["sw"] / 2
        img_r = img.rotate(-rotation, expand=True, resample=Image.BICUBIC)
        px = round(cx - img_r.width / 2)
        if item.get("anchor") == "bottom":
            bottom = item["y"] + item["sh"]
            py = round(bottom - img_r.height)
        else:
            cy = item["y"] + item["sh"] / 2
            py = round(cy - img_r.height / 2)
        img = img_r
    else:
        px, py = item["x"], item["y"]
    return img, px, py


def render_png(layout: dict, output_path: str, background_path: str = None,
                background_style: str = None, zoom_detail: dict = None,
                export_quality: bool = True, shadow: bool = True) -> None:
    """
    Composita las imágenes del layout en un canvas y guarda como PNG.

    layout: resultado de layout.compute_layout()
    output_path: ruta de salida .png
    background_path: imagen de fondo opcional, subida por el usuario (se
        escala al canvas completo; comportamiento sin cambios de siempre).
    background_style: id de un fondo de estudio de la galería (IA, ver
        STUDIO_BACKGROUND_STYLES). A diferencia de background_path, además
        activa reflejo + luz de borde + grading en cada producto — pensado
        para fondos oscuros/dramáticos donde esos efectos leen bien; se deja
        fuera de background_path para no cambiar el comportamiento de fondos
        propios que el usuario ya sube hoy.
    zoom_detail: dict opcional {"target": nombre|None, "region": {x,y,w,h}|None}
        — agrega una burbuja circular con un recorte ampliado de un producto
        (el "target", o si no se da el de mayor área) sobre la región dada
        (fracciones 0-1 relativas a SU propio recuadro). Sin esto no cambia
        nada del render normal.
    export_quality: si True (default), agranda el layout completo antes de
        componer para que el producto protagónico se acerque a su resolución
        ORIGINAL (ver _compute_export_scale) — el PNG final no debe quedarse
        en la resolución chica de preview solo porque el layout se calculó
        así. Ponelo en False para un render rápido/liviano (p. ej. previews).
    shadow: si False, apaga la sombra de contacto sin importar lo que pida
        la estrategia (todas la traen activada por defecto). Control manual
        del usuario — algunas composiciones/fondos se ven mejor sin ella.
    """
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow no instalado. Ejecuta: pip install Pillow")

    if export_quality:
        layout = _scale_layout(layout, _compute_export_scale(layout))

    cw = layout["canvas_width"]
    ch = layout["canvas_height"]

    canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))

    # Fondo/plantilla
    studio_effects = False
    if background_style:
        bg_name = STUDIO_BACKGROUND_STYLES.get(background_style)
        bg_file = (BACKGROUNDS_DIR / bg_name) if bg_name else None
        if bg_file and bg_file.exists():
            try:
                bg = Image.open(bg_file).convert("RGBA")
                bg = bg.resize((cw, ch), Image.LANCZOS)
                canvas.paste(bg, (0, 0), bg)
                studio_effects = True
            except Exception as e:
                print(f"Advertencia: no se pudo cargar el fondo de estudio: {e}")
        else:
            print(f"Advertencia: estilo de fondo desconocido: {background_style}")
    elif background_path and Path(background_path).exists():
        try:
            bg = Image.open(background_path).convert("RGBA")
            bg = bg.resize((cw, ch), Image.LANCZOS)
            canvas.paste(bg, (0, 0), bg)
        except Exception as e:
            print(f"Advertencia: no se pudo cargar el fondo: {e}")

    effects = layout.get("effects", {})
    shadow = shadow and bool(effects.get("shadow"))  # el toggle del usuario manda, pero no fuerza sombra donde la estrategia no la trae
    shadow_opacity = float(effects.get("shadow_opacity", 0.30))
    shadow_blur = int(effects.get("shadow_blur", 12))
    floating_shadow = bool(effects.get("floating_shadow"))

    # Ordenar por z para respetar la profundidad (lo de mayor z se pinta encima)
    items = sorted(layout["items"], key=lambda it: it.get("z", 0))

    # Sombras primero (en el mismo orden), para que ningún producto las tape
    if shadow:
        for item in items:
            fp = item.get("filepath", "")
            if not fp or not Path(fp).exists():
                continue
            try:
                size_scale = 1.0
                if floating_shadow:
                    size_scale = max(0.35, 1.0 - float(item.get("float_drop", 0.0)))
                _draw_contact_shadow(canvas, item, shadow_opacity, shadow_blur, size_scale=size_scale)
            except Exception as e:
                print(f"Advertencia: sombra fallida en {Path(fp).name}: {e}")

    # Productos — recortados a su contenido real. Se guarda la imagen final ya
    # rotada/gradeada por nombre, para que el zoom de detalle (más abajo) la
    # reuse tal cual quedó compuesta, sin repetir el trabajo.
    rendered = {}
    for item in items:
        fp = item.get("filepath", "")
        if not fp or not Path(fp).exists():
            continue
        try:
            # px/py: coordenadas del recuadro rotado respecto al lienzo. Con
            # anchor="bottom" (estrategias en fila, mismo borde inferior
            # compartido) el ancla al rotar es ESE borde; el resto ancla por
            # centro — ver _process_item_image.
            img, px, py = _process_item_image(item)

            if studio_effects:
                img = _studio_grade(img)
                canvas.alpha_composite(_studio_rim_glow(img), (px, py))
                canvas.alpha_composite(_studio_reflection(img), (px, py + img.height))

            canvas.alpha_composite(img, (px, py))
            rendered[item["name"]] = (img, px, py)
        except Exception as e:
            print(f"Advertencia: no se pudo renderizar {Path(fp).name}: {e}")

    if zoom_detail:
        try:
            _composite_zoom_detail(canvas, items, rendered, zoom_detail)
        except Exception as e:
            print(f"Advertencia: no se pudo componer el zoom de detalle: {e}")

    # El canvas se arma con margen de sobra (para que ninguna rotación ni la
    # sombra difuminada queden cortadas contra el borde durante el compose),
    # pero el margen "correcto" depende de qué se terminó dibujando de
    # verdad (sombra on/off, zoom de detalle, fondo de estudio...) — calcular
    # eso de antemano en layout.py implicaría duplicar esa lógica ahí. Más
    # simple y siempre exacto: recortar acá, al final, al contenido real ya
    # compuesto (mismo criterio de _content_bbox que ya usan las fotos de
    # producto en la subida) — el fondo transparente queda al ras de lo que
    # se ve, sin importar la estrategia ni los efectos activados. Si hay un
    # fondo propio o de estudio, el canvas queda opaco de punta a punta y
    # esto no recorta nada (correcto: ese fondo es intencional).
    bbox = _content_bbox(canvas)
    if bbox != (0, 0, cw, ch):
        canvas = canvas.crop(bbox)

    canvas.save(output_path, "PNG", optimize=True)


def render_psd(layout: dict, output_path: str, background_path: str = None,
                background_style: str = None, export_quality: bool = True,
                shadow: bool = True) -> None:
    """
    Genera un PSD con cada producto en su propia capa editable — a
    diferencia de render_png (todo aplanado en un solo PNG), esto se puede
    abrir en Photoshop y mover/reemplazar/ajustar cada producto por
    separado sin perder calidad ("para que la calidad se mantenga").

    Usa _process_item_image (misma función que render_png) para que ambos
    formatos salgan con la composición idéntica — el PSD no es una segunda
    implementación del render, es el mismo resultado con las capas sin
    aplanar.

    No soporta zoom_detail como capa separada (queda fuera del PSD si está
    activo). Los efectos de estudio (rim glow/reflejo), si están activos,
    quedan aplanados DENTRO de la capa del producto (no son su propia capa).
    shadow sí se separa en una capa "Sombras" única debajo de los productos.
    """
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow no instalado. Ejecuta: pip install Pillow")
    try:
        from psd_tools import PSDImage
    except ImportError as e:
        raise RuntimeError("Falta psd-tools. Ejecuta: pip install psd-tools") from e

    if export_quality:
        layout = _scale_layout(layout, _compute_export_scale(layout))

    cw = layout["canvas_width"]
    ch = layout["canvas_height"]

    canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    # [(nombre, imagen_rgba, left, top), ...] de atrás hacia adelante — el
    # orden de esta lista es el orden en que se agregan al PSD, y la última
    # agregada queda más arriba (verificado empíricamente, ver memoria).
    stack = []

    studio_effects = False
    if background_style:
        bg_name = STUDIO_BACKGROUND_STYLES.get(background_style)
        bg_file = (BACKGROUNDS_DIR / bg_name) if bg_name else None
        if bg_file and bg_file.exists():
            try:
                bg = Image.open(bg_file).convert("RGBA").resize((cw, ch), Image.LANCZOS)
                canvas.paste(bg, (0, 0), bg)
                stack.append(("Fondo", bg, 0, 0))
                studio_effects = True
            except Exception as e:
                print(f"Advertencia: no se pudo cargar el fondo de estudio: {e}")
        else:
            print(f"Advertencia: estilo de fondo desconocido: {background_style}")
    elif background_path and Path(background_path).exists():
        try:
            bg = Image.open(background_path).convert("RGBA").resize((cw, ch), Image.LANCZOS)
            canvas.paste(bg, (0, 0), bg)
            stack.append(("Fondo", bg, 0, 0))
        except Exception as e:
            print(f"Advertencia: no se pudo cargar el fondo: {e}")

    effects = layout.get("effects", {})
    shadow = shadow and bool(effects.get("shadow"))
    shadow_opacity = float(effects.get("shadow_opacity", 0.30))
    shadow_blur = int(effects.get("shadow_blur", 12))
    floating_shadow = bool(effects.get("floating_shadow"))

    items = sorted(layout["items"], key=lambda it: it.get("z", 0))

    if shadow:
        shadow_canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        for item in items:
            fp = item.get("filepath", "")
            if not fp or not Path(fp).exists():
                continue
            try:
                size_scale = 1.0
                if floating_shadow:
                    size_scale = max(0.35, 1.0 - float(item.get("float_drop", 0.0)))
                _draw_contact_shadow(shadow_canvas, item, shadow_opacity, shadow_blur, size_scale=size_scale)
            except Exception as e:
                print(f"Advertencia: sombra fallida en {Path(fp).name}: {e}")
        canvas.alpha_composite(shadow_canvas)
        stack.append(("Sombras", shadow_canvas, 0, 0))

    for item in items:
        fp = item.get("filepath", "")
        if not fp or not Path(fp).exists():
            continue
        try:
            img, px, py = _process_item_image(item)
            if studio_effects:
                img = _studio_grade(img)
                canvas.alpha_composite(_studio_rim_glow(img), (px, py))
                canvas.alpha_composite(_studio_reflection(img), (px, py + img.height))
            canvas.alpha_composite(img, (px, py))
            stack.append((item["name"], img, px, py))
        except Exception as e:
            print(f"Advertencia: no se pudo renderizar {Path(fp).name} para PSD: {e}")

    if not any(name not in ("Fondo", "Sombras") for name, *_ in stack):
        raise ValueError("No hay productos para exportar")

    # Mismo criterio de recorte que render_png — el PSD debe terminar exacto
    # donde termina el contenido real, igual que el PNG.
    bbox = _content_bbox(canvas)
    if bbox == (0, 0, cw, ch):
        crop_left, crop_top = 0, 0
    else:
        crop_left, crop_top, right, bottom = bbox
        canvas = canvas.crop(bbox)
    final_w, final_h = canvas.size

    compose_psd(
        [(name, img, left - crop_left, top - crop_top) for name, img, left, top in stack],
        (final_w, final_h),
        output_path,
        preview=canvas,
    )


def compose_psd(layers: list, size: tuple, output, preview=None) -> None:
    """Arma un PSD con una capa por elemento y lo guarda.

    layers: [(nombre, imagen_RGBA, left, top), ...] de ATRÁS hacia adelante —
            la última de la lista queda arriba en Photoshop.
    size:   (ancho, alto) del documento.
    output: ruta de archivo o file-like (BytesIO).
    preview: imagen ya compuesta para el preview aplanado. Si no viene, se
            compone acá apilando las capas en orden.

    Extraída de render_psd para que otros módulos (ej. promo.py, que arma un
    PSD con una capa por campo de la plantilla) reusen el mismo workaround en
    vez de duplicarlo: el auto-flatten interno de psd-tools al guardar
    (composite() sobre un documento recién creado) está roto — comprobado con
    datos reales, tira AssertionError de longitud al releer el archivo. Se
    inyecta el preview a mano, garantizando además que coincide exactamente
    con el PNG que ve el usuario.
    """
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow no instalado. Ejecuta: pip install Pillow")
    try:
        from psd_tools import PSDImage
    except ImportError as e:
        raise RuntimeError("Falta psd-tools. Ejecuta: pip install psd-tools") from e

    w, h = size
    if preview is None:
        preview = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        for _name, img, left, top in layers:
            preview.alpha_composite(img, (left, top))

    psd = PSDImage.new("RGBA", (w, h))
    for name, img, left, top in layers:
        psd.create_pixel_layer(img, name=name, top=top, left=left)

    # Comprimir el preview con RLE en vez de crudo. Sin esto el preview pesa
    # ancho x alto x 4 bytes sin comprimir — en una plantilla de promo real
    # (20226x6122) eso solo ya son 472 MB. Con RLE, las zonas transparentes
    # (que son casi todo) colapsan. Medido sobre un caso real.
    try:
        from psd_tools.constants import Compression
        psd._record.image_data.compression = Compression.RLE
    except (ImportError, AttributeError):
        pass    # sin compresión sigue siendo válido, solo más pesado

    channels_bytes = [c.tobytes() for c in preview.split()]
    psd._record.image_data.set_data(channels_bytes, psd._record.header)
    psd._updated = False

    psd.save(output)


def get_image_dimensions(filepath: str) -> tuple:
    """Retorna (width, height) del contenido real (recortado) de una imagen."""
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow no instalado")
    with Image.open(filepath) as img:
        img = img.convert("RGBA")
        l, t, r, b = _content_bbox(img)
        return (r - l, b - t)
