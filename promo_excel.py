"""Lógica de dominio de Promos: lectura de Excel → campos resueltos.

Portado SIN CAMBIOS desde el repo Generador-de-Promo
(https://github.com/lominet2020/Generador-de-Promo), archivo backend/main.py
líneas 41-763. Se mantiene idéntico a propósito: es lógica probada contra
casos reales (modos de PESOS, rich text por palabra, colores de tema de Excel)
y así se puede volver a sincronizar con el upstream sin resolver conflictos.

Acá NO hay nada de FastAPI ni de Still: son funciones puras sobre openpyxl.
La capa web vive en promo.py.
"""
import base64
import colorsys
import io
import json
import re
import unicodedata
from pathlib import Path
from xml.etree import ElementTree as ET

import openpyxl
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.utils import get_column_letter

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".svg")


class PromoExcelError(ValueError):
    """Excel inválido o sin datos utilizables.

    Excepción propia y no HTTPException: este módulo es deliberadamente
    agnóstico del framework (ver docstring de arriba). La capa web la traduce
    a un 400 en promo.py. El código portado del upstream sí levantaba
    HTTPException acá, lo que producía un NameError al no estar FastAPI
    importado — es decir, un Excel corrupto reventaba con un mensaje que no
    tenía nada que ver.
    """


def normalize(text: str) -> str:
    text = str(text)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9]", "", text)
    return text.upper()


def parse_markup_runs(text: str):
    """Sintaxis simple tipo WhatsApp: *negrilla*, ~tachado~, ^superíndice^.
    No soporta anidar marcadores.
    """
    pattern = re.compile(r"(\*[^*]+\*|~[^~]+~|\^[^^]+\^)")
    parts = pattern.split(text)
    runs = []
    for part in parts:
        if not part:
            continue
        if len(part) >= 2 and part.startswith("*") and part.endswith("*"):
            runs.append(_run(part[1:-1], bold=True))
        elif len(part) >= 2 and part.startswith("~") and part.endswith("~"):
            runs.append(_run(part[1:-1], strike=True))
        elif len(part) >= 2 and part.startswith("^") and part.endswith("^"):
            runs.append(_run(part[1:-1], superscript=True))
        else:
            runs.append(_run(part))
    return runs


def _run(text, bold=False, italic=False, strike=False, superscript=False, subscript=False, color=None):
    return {
        "text": text, "bold": bold, "italic": italic, "strike": strike,
        "superscript": superscript, "subscript": subscript, "color": color,
    }


def cell_font_color(cell, theme_colors=None):
    """Color de fuente explícito de una celda de openpyxl (o None si no hay
    celda, no tiene fuente, o el color no se pudo resolver a RGB). Envoltorio
    de _color_from_openpyxl que evita repetir el chequeo `cell is not None
    and cell.font` en cada punto donde se necesita el color de una celda
    completa (no de un bloque de rich text). `theme_colors`: paleta de 10
    colores base del workbook (ver parse_theme_colors), necesaria para
    resolver colores de tipo "theme" — sin ella, esos colores no se resuelven
    (se comportan como antes)."""
    return _color_from_openpyxl(cell.font.color, theme_colors) if (cell is not None and cell.font) else None


# Orden de los 10 colores base de un theme de Office/Excel según el índice
# que usa el atributo `theme` de un color de fuente (`<color theme="N">`):
# fondo1, texto1, fondo2, texto2, énfasis 1-6. OJO: este orden NO es el mismo
# que el orden en que aparecen en el XML del tema (<a:dk1>, <a:lt1>, ...) —
# dk1/lt1 y dk2/lt2 están invertidos entre el XML y el índice de estilos,
# es una particularidad conocida del formato OOXML.
THEME_INDEX_ORDER = ["lt1", "dk1", "lt2", "dk2",
                      "accent1", "accent2", "accent3", "accent4", "accent5", "accent6"]
THEME_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def parse_theme_colors(theme_xml_bytes):
    """Parsea el theme1.xml embebido en el workbook (wb.loaded_theme) y
    devuelve la lista de 10 colores base ("RRGGBB", en el orden de
    THEME_INDEX_ORDER) que necesita un color de fuente `theme="N"` para
    resolverse a RGB real. Devuelve None si no hay tema o no se pudo parsear
    (el llamador debe seguir sin resolver colores de tema en ese caso, igual
    que antes de este fix)."""
    if not theme_xml_bytes:
        return None
    try:
        root = ET.fromstring(theme_xml_bytes)
        scheme = root.find(f".//{THEME_NS}clrScheme")
        if scheme is None:
            return None

        def read(tag):
            el = scheme.find(f"{THEME_NS}{tag}")
            if el is None:
                return None
            srgb = el.find(f"{THEME_NS}srgbClr")
            if srgb is not None:
                return srgb.get("val")
            sys_clr = el.find(f"{THEME_NS}sysClr")
            if sys_clr is not None:
                return sys_clr.get("lastClr")
            return None

        colors = [read(tag) for tag in THEME_INDEX_ORDER]
        if any(c is None for c in colors):
            return None
        return colors
    except Exception:
        return None


def _apply_tint(hex_rgb, tint):
    """Aplica el ajuste de brillo ("tint") de Excel a un color base de tema
    —el mismo cálculo que produce variantes como "Azul, Énfasis 1, Claro
    40%"— ajustando la luminosidad en espacio HSL. Sin tint (0/None), el
    color queda igual."""
    if not tint:
        return hex_rgb
    r, g, b = int(hex_rgb[0:2], 16) / 255, int(hex_rgb[2:4], 16) / 255, int(hex_rgb[4:6], 16) / 255
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    if tint < 0:
        l = l * (1.0 + tint)
    else:
        l = l * (1.0 - tint) + tint
    l = max(0.0, min(1.0, l))
    r2, g2, b2 = colorsys.hls_to_rgb(h, l, s)
    return f"{round(r2 * 255):02X}{round(g2 * 255):02X}{round(b2 * 255):02X}"


def _color_from_openpyxl(color, theme_colors=None):
    """Convierte un Color de openpyxl (el de una fuente) a 'rgb(r,g,b)' para
    el frontend. Resuelve dos casos:
      1. RGB explícito (aRGB/RGB de 6 u 8 dígitos hex) — el que queda guardado
         al elegir un color desde "Colores estándar"/"Más colores" en Excel.
      2. Color de TEMA (`type == "theme"`, ej. "Azul, Énfasis 1") — el que
         queda guardado al elegir un color desde la fila de arriba del
         selector ("Colores del tema"), que en la práctica es el caso más
         común (así es como Excel guarda incluso varios de los "Colores
         estándar" de la fila de abajo). Se resuelve contra `theme_colors`
         (la paleta de 10 colores del workbook, ver parse_theme_colors) +
         el ajuste de brillo (`tint`) de la celda.

    OJO con el caso 2: el índice de tema 0 ("Fondo 1"/lt1) y 1 ("Texto
    1"/dk1) son los que trae CUALQUIER celda sin tocar (el negro/blanco
    "automático" de la plantilla de Excel) — no una elección real de color
    de quien llenó el Excel. Por eso, si el índice es 0 o 1 Y no tiene tint
    (el caso normal de una celda nunca tocada), se trata iguel que "sin color
    explícito" (None) — así el campo sigue usando el color base de la
    plantilla, sin romper filas que nunca tuvieron color explícito. Si SÍ
    trae tint (ej. alguien eligió a propósito "Texto 1, Claro 25%"), se
    respeta como una elección real.
    """
    if color is None:
        return None
    ctype = getattr(color, "type", None)

    if ctype == "rgb":
        rgb = color.rgb
        if not isinstance(rgb, str):
            return None
        if len(rgb) == 8:  # AARRGGBB
            r, g, b = rgb[2:4], rgb[4:6], rgb[6:8]
        elif len(rgb) == 6:  # RRGGBB
            r, g, b = rgb[0:2], rgb[2:4], rgb[4:6]
        else:
            return None
        try:
            return f"rgb({int(r, 16)},{int(g, 16)},{int(b, 16)})"
        except ValueError:
            return None

    if ctype == "theme" and theme_colors is not None:
        idx = color.theme
        tint = color.tint or 0.0
        if idx is None or idx < 0 or idx >= len(theme_colors):
            return None
        if idx in (0, 1) and abs(tint) < 1e-6:
            return None  # fondo/texto "automático" de una celda sin tocar
        hex_rgb = _apply_tint(theme_colors[idx], tint)
        try:
            return f"rgb({int(hex_rgb[0:2], 16)},{int(hex_rgb[2:4], 16)},{int(hex_rgb[4:6], 16)})"
        except ValueError:
            return None

    return None


AUTO_SUPERSCRIPT_CHARS = "®™©"


def apply_auto_superscript(runs):
    """Separa símbolos como ®/™/© en su propio run con superíndice automático,
    sin que nadie tenga que marcarlos a mano con ^®^ en el Excel. Se aplica
    sobre CUALQUIER run (venga de rich text real o de sintaxis de marcado),
    conservando el resto de su estilo (negrilla/color/etc. si lo tenía).
    """
    pattern = re.compile(f"([{re.escape(AUTO_SUPERSCRIPT_CHARS)}])")
    result = []
    for r in runs:
        segments = pattern.split(r["text"])
        for seg in segments:
            if seg == "":
                continue
            new_run = dict(r)
            new_run["text"] = seg
            if seg in AUTO_SUPERSCRIPT_CHARS:
                new_run["superscript"] = True
            result.append(new_run)
    return result


def cell_to_runs(value, cell=None, theme_colors=None):
    """Combina DOS mecanismos para formato mixto dentro de una celda (el que sea
    que haya usado quien llenó el Excel):
      1. Rich text REAL de Excel: seleccionar una palabra y ponerla en negrilla/
         tachado/color/etc. con el mouse (openpyxl lo expone como CellRichText).
      2. Sintaxis de marcado en el texto plano: *negrilla*, ~tachado~, ^superíndice^
         (más simple y a prueba de "se perdió el formato al copiar/pegar").
    Además, símbolos como ® ™ © se ponen en superíndice automáticamente, sin
    necesidad de marcarlos.
    Si la celda usa rich text real, el texto de cada bloque también se pasa por el
    parser de marcado (por si alguien combina ambos mecanismos).

    Color: si `cell` viene (la celda de openpyxl, no solo su valor), se lee el
    color de fuente elegido a mano en Excel — tanto el de la celda completa
    (`cell.font.color`, cuando el texto no tiene rich text de por medio) como
    el de cada bloque de rich text por separado (`block.font.color`, cuando
    distintas partes del mismo texto tienen distinto color). Si no hay un
    color explícito (celda sin tocar, color "automático"), el run queda con
    color=None y el frontend usa el color base de la plantilla, como siempre
    — así no se pisa el color de la plantilla para las filas de siempre.
    `theme_colors` (paleta del workbook, ver parse_theme_colors) hace falta
    para resolver colores elegidos desde la fila de "Colores del tema" del
    selector de Excel — el caso más común en la práctica.
    """
    cell_color = _color_from_openpyxl(cell.font.color, theme_colors) if (cell is not None and cell.font) else None

    if isinstance(value, CellRichText):
        runs = []
        for block in value:
            if isinstance(block, TextBlock):
                f = block.font
                block_color = _color_from_openpyxl(f.color, theme_colors) if f else None
                for r in parse_markup_runs(block.text):
                    runs.append(_run(
                        r["text"],
                        bold=bool(f.b) or r["bold"],
                        italic=bool(f.i) or r["italic"],
                        strike=bool(f.strike) or r["strike"],
                        superscript=(f.vertAlign == "superscript") or r["superscript"],
                        subscript=(f.vertAlign == "subscript") or r["subscript"],
                        color=block_color or cell_color,
                    ))
            else:
                for r in parse_markup_runs(str(block)):
                    r["color"] = r["color"] or cell_color
                    runs.append(r)
        return apply_auto_superscript(runs)
    value_str = "" if value is None else str(value).strip()
    runs = parse_markup_runs(value_str)
    for r in runs:
        r["color"] = r["color"] or cell_color
    return apply_auto_superscript(runs)


MULTIPLIER_COLUMN_NAME = "MULTIPLICADOR"
PRICE_COLUMN_NAME = "PESOS O PRECIO"
DISCOUNT_COLUMN_NAME = "DESCUENTO"
MULTIPLIER2_COLUMN_NAME = "MULTIPLICADOR 2"
PRICE2_COLUMN_NAME = "PESOS O PRECIO 2"
DESCRIPCION2_COLUMN_NAME = "DESCRIPCION 2"
PESOS_OUTPUT_FIELD = "PESOS"  # nombre sintético que espera template.json
DOBLE_DESC_OUTPUT_FIELD = "PROMO_DOBLE_DESCRIPCION"  # nombre sintético para la variante nueva
SEGUNDO_POR_OUTPUT_FIELD = "PROMO_SEGUNDO_POR"  # nombre sintético para la variante "y llévate el segundo por"
SOLO_FRASE_DESC_OUTPUT_FIELD = "PROMO_SOLO_FRASE_DESC"  # variante: solo frase de apoyo + descripción (8 líneas)
SOLO_DESCRIPCION_OUTPUT_FIELD = "PROMO_SOLO_DESCRIPCION"  # variante: solo descripción (20 líneas, sin callout/marca)

CONNECTOR_PATTERN = re.compile(r"(?:^|\s)[oOóÓ](?:\s|$)")
SEGUNDO_POR_TRIGGER_PATTERN = re.compile(r"y\s+ll[eé]va(?:te)?\s+el\s+segundo(?:\s+por)?", re.IGNORECASE)


def remove_phrase_from_runs(runs, pattern):
    """Quita la primera coincidencia de `pattern` del texto concatenado de los
    runs, preservando el estilo (negrilla/tachado/etc.) de las partes que NO se
    tocan — no aplana todo a texto plano. Colapsa los espacios dobles que
    puedan quedar tras el recorte. Devuelve (runs_nuevos, texto_plano_nuevo).
    """
    full_text = "".join(r["text"] for r in runs)
    m = pattern.search(full_text)
    if not m:
        return runs, full_text

    start, end = m.span()
    new_runs = []
    pos = 0
    for r in runs:
        r_start, r_end = pos, pos + len(r["text"])
        pos = r_end
        before_end = min(r_end, start)
        if before_end > r_start:
            new_runs.append({**r, "text": r["text"][: before_end - r_start]})
        after_start = max(r_start, end)
        if after_start < r_end:
            new_runs.append({**r, "text": r["text"][after_start - r_start :]})

    cleaned_runs = []
    for r in new_runs:
        text = re.sub(r"\s{2,}", " ", r["text"])
        if text != "":
            cleaned_runs.append({**r, "text": text})

    new_text = re.sub(r"\s{2,}", " ", "".join(r["text"] for r in cleaned_runs)).strip()
    return cleaned_runs, new_text


def parse_percent_value(value_str):
    """Modo porcentaje: número (máximo 2 cifras) + "%" + "desc" (fijo, siempre
    "desc" sin importar si el Excel dice "5%" o "5% descuento" — el texto que se
    muestra es siempre "desc"), y opcionalmente "c/u" al final.
    Ej: "5%"              -> number="5",  has_cu=False
        "5% descuento"    -> number="5",  has_cu=False  (igual, solo hasta "desc")
        "23% c/u"         -> number="23", has_cu=True
    """
    text = value_str.strip()
    has_cu = False
    m = re.match(r"^(.*?)\s*c/u\s*$", text, re.IGNORECASE)
    if m:
        text = m.group(1).strip()
        has_cu = True

    m2 = re.match(r"^(\d+)", text)
    number = m2.group(1)[:2] if m2 else re.sub(r"\D", "", text)[:2]
    return {"mode": "percent", "number": number, "has_cu": has_cu}


def parse_quantity_value(text):
    """Modo cantidad: '2x3', '3X4', '6X2' (case-insensitive) — promos tipo
    "lleva N por M". Sin "$", sin "%", sin "desc", sin "c/u": solo los dos
    números, cada uno recortado a máximo 2 cifras.
    Devuelve None si el texto no matchea el patrón (para que se siga probando
    con los otros modos).
    """
    m = re.match(r"^(\d+)\s*[xX]\s*(\d+)$", text.strip())
    if not m:
        return None
    return {"mode": "quantity", "left": m.group(1)[:2], "right": m.group(2)[:2]}


def parse_price_only(text):
    """Extrae de un texto de precio (columna PESOS O PRECIO):
      - la parte entera (los dígitos grandes) — hasta 4 cifras en modo precio solo
      - los centavos (si trae punto o coma decimal) — máximo 2 dígitos
      - si trae el sufijo "c/u" al final (con o sin espacio antes)
    Ej: "10.25 c/u" -> integer="10", cents="25", has_cu=True
        "99"         -> integer="99", cents=None, has_cu=False
    """
    text = text.strip()
    has_cu = False
    m = re.match(r"^(.*?)\s*c/u\s*$", text, re.IGNORECASE)
    if m:
        text = m.group(1).strip()
        has_cu = True

    integer_part = text
    cents = None
    parts = re.split(r"[.,]", text, maxsplit=1)
    if len(parts) == 2:
        integer_part = parts[0].strip()
        cents = re.sub(r"\D", "", parts[1])[:2] or None
    integer_part = re.sub(r"\D", "", integer_part)[:4]

    return {"mode": "price", "integer": integer_part, "cents": cents, "has_cu": has_cu}


def build_multiplier_price_line(mult_text, price_text, max_price_digits, multiplier_color=None, price_color=None):
    """Arma una 'línea' de multiplicador+precio (ej. '2x$45'), reutilizada tanto
    por el modo combinado (una sola línea o dividida en 2) como por el modo
    doble (dos líneas independientes, cada una con este mismo formato).
    Devuelve None si no matchea (multiplicador no es un número simple, o falta
    el precio).

    `multiplier_color`/`price_color`: color de fuente explícito leído de las
    celdas MULTIPLICADOR/PESOS O PRECIO (o None si la celda no tiene un color
    elegido a mano) — se agregan tal cual al resultado para que el frontend
    los use como override del color fijo de la plantilla, célda por célda.
    """
    m = re.match(r"^(\d+)$", mult_text)
    if not m or not price_text:
        return None
    multiplier = m.group(1)[:2]
    price_parsed = parse_price_only(price_text)
    return {
        "multiplier": multiplier,
        "integer": price_parsed["integer"][:max_price_digits],
        "cents": price_parsed["cents"],
        "has_cu": price_parsed["has_cu"],
        "multiplier_color": multiplier_color,
        "color": price_color,
    }


def compute_pesos_field(multiplier_cell, price_cell, discount_cell, multiplier2_cell=None, price2_cell=None, theme_colors=None):
    """Combina las columnas (MULTIPLICADOR, PESOS O PRECIO, DESCUENTO, y
    opcionalmente MULTIPLICADOR 2 / PESOS O PRECIO 2) en una sola estructura de
    precio para la plantilla, según qué venga lleno:

      0. Las 4 columnas de multiplicador/precio (1 y 2) tienen algo -> modo
         DOBLE: dos líneas "Nx$PPPP" apiladas (multiplicador máx. 2 cifras,
         precio máx. 4 cifras cada línea). Es el patrón más específico (más
         columnas involucradas), así que tiene la prioridad más alta — se
         revisa antes que DESCUENTO.
      1. DESCUENTO tiene algo -> modo PORCENTAJE (usa esa columna).
      2. MULTIPLICADOR trae "NxM" explícito -> modo CANTIDAD.
      3. MULTIPLICADOR trae un solo número Y PESOS O PRECIO también tiene algo
         -> modo COMBINADO: "Nx$PPP" (multiplicador máx. 2 cifras, precio máx.
         3 cifras). Si el precio queda en 3 cifras, el layout se arma vertical
         (multiplicador+x arriba, $+precio abajo); si queda en 2 o menos,
         horizontal (todo en una línea) — la decisión se toma en el frontend
         según la cantidad de cifras que realmente tenga el precio.
      4. Solo PESOS O PRECIO tiene algo -> modo PRECIO normal (hasta 4 cifras).
      5. Nada de lo anterior -> None (no se muestra el campo).

    Color: al igual que con cell_to_runs, cada celda involucrada
    (MULTIPLICADOR/PESOS O PRECIO/DESCUENTO y sus variantes "2") puede traer
    un color de fuente elegido a mano en Excel — se lee acá con
    _color_from_openpyxl y se agrega al resultado (como "color"/
    "multiplier_color", según el modo) para que el frontend lo use como
    override del color fijo de la plantilla. Si la celda no tiene un color
    explícito, queda None y el frontend sigue usando el color de la
    plantilla, como siempre.
    """
    def cell_text(cell):
        if cell is None or cell.value is None:
            return ""
        return str(cell.value).strip()

    def cell_color(cell):
        return _color_from_openpyxl(cell.font.color, theme_colors) if (cell is not None and cell.font) else None

    mult_text = cell_text(multiplier_cell)
    price_text = cell_text(price_cell)
    discount_text = cell_text(discount_cell)
    mult2_text = cell_text(multiplier2_cell)
    price2_text = cell_text(price2_cell)

    mult_color = cell_color(multiplier_cell)
    price_color = cell_color(price_cell)
    discount_color = cell_color(discount_cell)
    mult2_color = cell_color(multiplier2_cell)
    price2_color = cell_color(price2_cell)

    # 0. Modo doble: las 4 columnas de multiplicador/precio llenas
    if mult_text and price_text and mult2_text and price2_text:
        line1 = build_multiplier_price_line(mult_text, price_text, max_price_digits=4,
                                             multiplier_color=mult_color, price_color=price_color)
        line2 = build_multiplier_price_line(mult2_text, price2_text, max_price_digits=4,
                                             multiplier_color=mult2_color, price_color=price2_color)
        if line1 and line2:
            return {"mode": "double", "line1": line1, "line2": line2}

    # OJO: una celda puede tener formato "0%" aplicado aunque esté VACÍA (por
    # ejemplo si alguna vez tuvo un valor y se borró, o si el formato se aplicó
    # a toda la columna) — por eso discount_is_percent_fmt exige también que
    # haya un valor numérico real, no solo que el formato diga "%".
    discount_is_percent_fmt = bool(
        discount_cell is not None
        and isinstance(discount_cell.value, (int, float))
        and discount_cell.number_format
        and "%" in discount_cell.number_format
    )

    # 1. Descuento (porcentaje)
    if discount_text or discount_is_percent_fmt:
        if discount_is_percent_fmt:
            n = discount_cell.value * 100
            discount_text = f"{int(n)}%" if n == int(n) else f"{n:.2f}%"
        return {**parse_percent_value(discount_text), "color": discount_color}

    # 2. Multiplicador "NxM" explícito
    quantity = parse_quantity_value(mult_text) if mult_text else None
    if quantity:
        return {**quantity, "color": mult_color}

    # 3. Multiplicador (número simple) + precio -> combinado
    combined = build_multiplier_price_line(mult_text, price_text, max_price_digits=3,
                                            multiplier_color=mult_color, price_color=price_color)
    if combined:
        return {"mode": "combined", **combined}

    # 4. Solo precio
    if price_text:
        return {**parse_price_only(price_text), "color": price_color}

    return None


def load_workbook_rows(content):
    """Carga el workbook y devuelve (ws, headers, data_rows, theme_colors) —
    filas/columnas ocultas ya excluidas, headers de la primera fila visible,
    data_rows es la lista de TODAS las filas de datos visibles con al menos
    un valor (no solo la primera), y theme_colors es la paleta de 10 colores
    del tema del workbook (ver parse_theme_colors) — necesaria para resolver
    colores de fuente elegidos desde la fila de "Colores del tema" del
    selector de Excel (el caso más común en la práctica, más común incluso
    que el RGB explícito). Todo esto se reutiliza tanto para una sola
    promoción como para el modo por lotes.
    """
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, rich_text=True)
    except Exception as e:
        raise PromoExcelError(f"No se pudo leer el Excel: {e}")

    theme_colors = parse_theme_colors(getattr(wb, "loaded_theme", None))
    ws = wb.active

    def is_row_hidden(row_idx):
        dim = ws.row_dimensions.get(row_idx)
        return bool(dim and dim.hidden)

    def is_col_hidden(col_idx):
        letter = get_column_letter(col_idx)
        dim = ws.column_dimensions.get(letter)
        return bool(dim and dim.hidden)

    visible_rows = [row for i, row in enumerate(ws.iter_rows(), start=1) if not is_row_hidden(i)]
    if len(visible_rows) < 2:
        raise PromoExcelError("El Excel no tiene datos visibles (se esperaba fila de headers + al menos 1 fila de promoción, sin contar filas/columnas ocultas)")

    def visible_cells(row):
        return [c for c in row if not is_col_hidden(c.column)]

    header_row = visible_cells(visible_rows[0])
    headers = [str(c.value).strip() if c.value is not None else "" for c in header_row]

    data_rows = []
    for row in visible_rows[1:]:
        cells = visible_cells(row)
        if any(c.value is not None and str(c.value).strip() != "" for c in cells):
            data_rows.append(cells)

    return ws, headers, data_rows, theme_colors


def extract_fields_from_row(headers, data_cells, theme_colors=None):
    """Procesa UNA fila de datos (headers ya emparejados con celdas) y devuelve
    la lista de fields — toda la lógica de columnas/modos/variantes especiales,
    reutilizada tanto para una sola promoción (/api/read-excel) como para el
    modo por lotes (/api/read-excel-batch), fila por fila. `theme_colors`: ver
    load_workbook_rows/parse_theme_colors.
    """
    fields = []
    cells_by_normalized_header = {}
    for header, cell in zip(headers, data_cells):
        if not header:
            continue
        value = cell.value
        value_str = "" if value is None else str(value).strip()
        is_image = value_str.lower().endswith(IMAGE_EXTENSIONS)
        field = {
            "column": header,
            "column_normalized": normalize(header),
            "type": "image" if is_image else "text",
        }
        if is_image:
            field["value"] = value_str
        else:
            runs = cell_to_runs(value, cell, theme_colors)
            field["runs"] = runs
            field["value"] = "".join(r["text"] for r in runs)
        fields.append(field)
        cells_by_normalized_header[normalize(header)] = cell

    # Campo sintético "PESOS": combina MULTIPLICADOR + PESOS O PRECIO + DESCUENTO
    # (3 columnas separadas del Excel) en una sola estructura para la plantilla.
    # Se agrega como un field aparte, con nombre "PESOS" — es el nombre que
    # template.json espera, independiente de cómo se llamen las columnas reales.
    multiplier_cell = cells_by_normalized_header.get(normalize(MULTIPLIER_COLUMN_NAME))
    price_cell = cells_by_normalized_header.get(normalize(PRICE_COLUMN_NAME))
    discount_cell = cells_by_normalized_header.get(normalize(DISCOUNT_COLUMN_NAME))
    multiplier2_cell = cells_by_normalized_header.get(normalize(MULTIPLIER2_COLUMN_NAME))
    price2_cell = cells_by_normalized_header.get(normalize(PRICE2_COLUMN_NAME))
    price_data = compute_pesos_field(multiplier_cell, price_cell, discount_cell, multiplier2_cell, price2_cell, theme_colors)

    # Conector "O"/"o"/"Ó"/"ó": si aparece como palabra suelta en DESCRIPCION,
    # es una SEÑAL que activa la variante con conector del modo doble
    # ("0x$000 / ó0x$000") — pero la descripción se sigue mostrando TAL CUAL,
    # con la "o"/"ó" incluida (sigue siendo parte de la frase real y debe
    # leerse bien). Solo se usa para activar el campo PESOS, no se toca el
    # texto de la descripción.
    if price_data and price_data.get("mode") == "double":
        descripcion_field = next(
            (f for f in fields if f["column_normalized"] == normalize("DESCRIPCION")), None
        )
        if descripcion_field and CONNECTOR_PATTERN.search(descripcion_field.get("value", "")):
            price_data["connector"] = "ó"

    if price_data:
        fields.append({
            "column": PESOS_OUTPUT_FIELD,
            "column_normalized": normalize(PESOS_OUTPUT_FIELD),
            "type": "text",
            "value": json.dumps(price_data, ensure_ascii=False),  # solo para el preview/debug
            "runs": [],
            "price": price_data,
        })

    # Variante "promo doble descripción": se activa cuando el Excel trae
    # DESCRIPCION 2 con algo. Reemplaza FRASE APOYO/PESOS/DESCRIPCION/BRAND por
    # una estructura nueva: "COMPRA" (fijo) + descripción 1 + [caso] + descripción 2.
    #   - Caso 1 (PESOS O PRECIO tiene algo): "+$NN" (máx. 2 cifras) + banner
    #     fijo "LLÉVATE" — el CALLOUT NUNCA se muestra en este caso, aunque el
    #     Excel traiga uno.
    #   - Caso 2 (PESOS O PRECIO vacío): banner fijo "Y LLÉVATE" + "GRATIS" en
    #     negrilla — el CALLOUT sí puede aparecer (dinámico, del Excel), igual
    #     que en la plantilla normal.
    descripcion2_cell = cells_by_normalized_header.get(normalize(DESCRIPCION2_COLUMN_NAME))
    descripcion2_text = "" if descripcion2_cell is None or descripcion2_cell.value is None else str(descripcion2_cell.value).strip()
    if descripcion2_text:
        has_price = bool(price_cell is not None and price_cell.value is not None and str(price_cell.value).strip() != "")
        doble_desc_data = {"case": 1 if has_price else 2, "suppress_callout": has_price}
        if has_price:
            price_only = parse_price_only(str(price_cell.value).strip())
            doble_desc_data["price_integer"] = price_only["integer"][:2]
            doble_desc_data["price_color"] = cell_font_color(price_cell, theme_colors)

        # DESCRIPCION 2 se procesa con las mismas reglas que DESCRIPCION (rich
        # text + marcadores + auto-superíndice), como un field normal más.
        desc2_runs = cell_to_runs(descripcion2_cell.value, descripcion2_cell, theme_colors)
        fields.append({
            "column": DESCRIPCION2_COLUMN_NAME,
            "column_normalized": normalize(DESCRIPCION2_COLUMN_NAME),
            "type": "text",
            "value": "".join(r["text"] for r in desc2_runs),
            "runs": desc2_runs,
        })
        fields.append({
            "column": DOBLE_DESC_OUTPUT_FIELD,
            "column_normalized": normalize(DOBLE_DESC_OUTPUT_FIELD),
            "type": "text",
            "value": json.dumps(doble_desc_data, ensure_ascii=False),
            "runs": [],
            "doble_desc": doble_desc_data,
        })
    else:
        # Variante "y llévate el segundo por": se activa cuando esa frase
        # aparece dentro de DESCRIPCION (no se quita del texto — sigue leyéndose
        # igual que antes, la frase es solo la señal). Estructura: CALLOUT
        # (opcional, dinámico) + COMPRA (fijo) + descripción (1 sola, máx. 4
        # líneas con el mismo responsive de siempre) + banner fijo "Y LLÉVATE" +
        # texto fijo "EL SEGUNDO CON" + [caso 1: $NN (máx. 2 cifras) | caso 2:
        # NN% desc]. Sin BRAND en ningún caso. Solo se revisa si NO se activó ya
        # la variante de doble descripción (son señales distintas, pero por las
        # dudas no se evalúan las dos a la vez).
        descripcion_field = next(
            (f for f in fields if f["column_normalized"] == normalize("DESCRIPCION")), None
        )
        if descripcion_field and SEGUNDO_POR_TRIGGER_PATTERN.search(descripcion_field.get("value", "")):
            has_price = bool(price_cell is not None and price_cell.value is not None and str(price_cell.value).strip() != "")
            has_discount = bool(discount_cell is not None and discount_cell.value is not None and str(discount_cell.value).strip() != "") or bool(
                discount_cell is not None
                and isinstance(discount_cell.value, (int, float))
                and discount_cell.number_format
                and "%" in discount_cell.number_format
            )
            if has_price:
                price_only = parse_price_only(str(price_cell.value).strip())
                segundo_por_data = {"case": 1, "price_integer": price_only["integer"][:2], "price_color": cell_font_color(price_cell, theme_colors)}
            elif has_discount:
                if isinstance(discount_cell.value, (int, float)) and discount_cell.number_format and "%" in discount_cell.number_format:
                    n = discount_cell.value * 100
                    discount_number = str(int(n)) if n == int(n) else f"{n:.2f}"
                else:
                    m = re.match(r"^(\d+)", str(discount_cell.value).strip())
                    discount_number = m.group(1) if m else re.sub(r"\D", "", str(discount_cell.value))
                segundo_por_data = {"case": 2, "percent_number": discount_number[:2], "price_color": cell_font_color(discount_cell, theme_colors)}
            else:
                segundo_por_data = None

            if segundo_por_data:
                # A diferencia del conector "o"/"ó" (que se deja tal cual), acá
                # SÍ se quita la frase disparadora de la descripción — ya que el
                # banner fijo "Y LLÉVATE" + el texto fijo "EL SEGUNDO CON" que
                # se muestran más abajo la vuelven redundante si se queda.
                cleaned_runs, cleaned_value = remove_phrase_from_runs(
                    descripcion_field["runs"], SEGUNDO_POR_TRIGGER_PATTERN
                )
                descripcion_field["runs"] = cleaned_runs
                descripcion_field["value"] = cleaned_value

                fields.append({
                    "column": SEGUNDO_POR_OUTPUT_FIELD,
                    "column_normalized": normalize(SEGUNDO_POR_OUTPUT_FIELD),
                    "type": "text",
                    "value": json.dumps(segundo_por_data, ensure_ascii=False),
                    "runs": [],
                    "segundo_por": segundo_por_data,
                })

    # Las 2 variantes más simples: se activan por EXCLUSIÓN — solo cuando no
    # hay absolutamente nada de MULTIPLICADOR/PESOS O PRECIO/DESCUENTO (ni la
    # frase disparadora de "segundo por"), y no hay DESCRIPCION 2. En ese caso,
    # lo único que importa es si viene FRASE APOYO o no:
    #   - FRASE APOYO + DESCRIPCION (nada más) -> descripción hasta 8 líneas,
    #     CALLOUT y BRAND se muestran normal (dinámicos, si el Excel los trae).
    #   - Solo DESCRIPCION (sin frase de apoyo) -> descripción hasta 20 líneas,
    #     usando el alto total de la plantilla. CALLOUT y BRAND NUNCA se
    #     muestran acá, aunque el Excel los traiga.
    no_special_price_data = price_data is None and not descripcion2_text

    descripcion_field2 = next(
        (f for f in fields if f["column_normalized"] == normalize("DESCRIPCION")), None
    )
    frase_field = next(
        (f for f in fields if f["column_normalized"] == normalize("FRASE APOYO")), None
    )
    has_frase = bool(frase_field and frase_field.get("value", "").strip())
    has_desc = bool(descripcion_field2 and descripcion_field2.get("value", "").strip())

    if no_special_price_data and has_desc:
        if has_frase:
            fields.append({
                "column": SOLO_FRASE_DESC_OUTPUT_FIELD,
                "column_normalized": normalize(SOLO_FRASE_DESC_OUTPUT_FIELD),
                "type": "text",
                "value": "true",
                "runs": [],
                "solo_frase_desc": True,
            })
        else:
            fields.append({
                "column": SOLO_DESCRIPCION_OUTPUT_FIELD,
                "column_normalized": normalize(SOLO_DESCRIPCION_OUTPUT_FIELD),
                "type": "text",
                "value": "true",
                "runs": [],
                "solo_descripcion": True,
            })

    return fields


