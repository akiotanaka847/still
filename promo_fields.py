"""Constructor de `fields[]` agnóstico del origen de los datos.

Equivalente de promo_excel.extract_fields_from_row / compute_pesos_field, pero
operando sobre el contrato neutro `Cell` (ver promo_source.py) en vez de sobre
celdas de openpyxl. Es lo que permite alimentar el motor de promos desde JSON,
CSV o una API.

**Toda la lógica de negocio se IMPORTA de promo_excel, no se duplica**: los
parsers de precio/porcentaje/cantidad, el manejo de runs, la normalización de
nombres y las constantes de columna son los mismos objetos. Lo único que se
reescribe acá es el ACCESO AL DATO — que es exactamente lo que estaba atado a
Excel:

    cell.value                      -> cell["value"]
    cell.font.color                 -> cell["color"]      (ya resuelto)
    "%" in cell.number_format       -> cell["is_percent"]
    CellRichText                    -> cell["runs"]

El resultado debe ser BYTE A BYTE idéntico al de promo_excel para los mismos
datos. Eso no es una aspiración: lo verifica tests/test_source_equivalence.py
contra las 128 filas del golden. Si las dos copias divergen, ese test falla.

Por qué existe una segunda copia en vez de refactorizar el original: el módulo
de Promos actual funciona y no se toca (decisión explícita del usuario), y
promo_excel.py se porta tal cual del repo upstream para poder resincronizarlo.
El costo aceptado es mantener dos copias; el golden es lo que evita que se
desincronicen en silencio.
"""
import json
import re

from promo_excel import (
    ALCOHOL_COLUMNS,
    ALCOHOL_OUTPUT_FIELD,
    CONNECTOR_PATTERN,
    DESCRIPCION2_COLUMN_NAME,
    DISCOUNT_COLUMN_NAME,
    DOBLE_DESC_OUTPUT_FIELD,
    IMAGE_EXTENSIONS,
    MULTIPLIER2_COLUMN_NAME,
    MULTIPLIER_COLUMN_NAME,
    PESOS_OUTPUT_FIELD,
    PRICE2_COLUMN_NAME,
    PRICE_COLUMN_NAME,
    SEGUNDO_POR_OUTPUT_FIELD,
    SEGUNDO_POR_TRIGGER_PATTERN,
    SOLO_DESCRIPCION_OUTPUT_FIELD,
    SOLO_FRASE_DESC_OUTPUT_FIELD,
    apply_auto_superscript,
    build_multiplier_price_line,
    looks_like_alcohol,
    normalize,
    parse_markup_runs,
    parse_percent_value,
    parse_price_only,
    parse_quantity_value,
    remove_phrase_from_runs,
)


# ─── Acceso al dato (lo único que cambia respecto de promo_excel) ────────────
def cell_text(cell) -> str:
    if cell is None or cell.get("value") is None:
        return ""
    return str(cell["value"]).strip()


def cell_color(cell):
    return cell.get("color") if cell else None


def cell_is_percent(cell) -> bool:
    """True si el origen declara que el valor es un porcentaje.

    En Excel esto venía de `number_format` (un `10%` se guarda como `0.1` con
    formato `0%`). En JSON se declara explícitamente. Se exige además que el
    valor sea numérico, por el mismo motivo que el original: una celda puede
    tener formato de porcentaje aunque esté vacía.
    """
    return bool(
        cell
        and cell.get("is_percent")
        and isinstance(cell.get("value"), (int, float))
    )


def cell_to_runs(cell) -> list:
    """Runs de una celda neutra.

    Si el origen trae runs explícitos (JSON nivel 2), se respetan y solo se les
    hereda el color de la celda donde no lo definan. Si no, se derivan del
    texto con la sintaxis de marcas (`*negrilla*`, `~tachado~`, `^sup^`) —
    exactamente el mismo camino que usa promo_excel para una celda sin rich
    text.
    """
    if cell is None:
        return apply_auto_superscript(parse_markup_runs(""))

    color = cell.get("color")
    explicit = cell.get("runs")
    if explicit:
        runs = []
        for r in explicit:
            r = dict(r)
            if r.get("color") is None:
                r["color"] = color
            runs.append(r)
        return apply_auto_superscript(runs)

    value = cell.get("value")
    value_str = "" if value is None else str(value).strip()
    runs = parse_markup_runs(value_str)
    if color:
        runs = [{**r, "color": r["color"] if r["color"] else color} for r in runs]
    return apply_auto_superscript(runs)


# ─── Campo sintético PESOS ───────────────────────────────────────────────────
def compute_pesos_field(multiplier_cell, price_cell, discount_cell,
                        multiplier2_cell=None, price2_cell=None):
    """Combina las columnas de multiplicador/precio/descuento en un solo campo.

    Mismo árbol de decisión y mismas prioridades que promo_excel.compute_pesos_field
    (doble -> porcentaje -> cantidad -> combinado -> precio): esa lógica ya
    operaba sobre texto y color, no sobre celdas.
    """
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

    discount_is_percent_fmt = cell_is_percent(discount_cell)

    # 1. Descuento (porcentaje)
    if discount_text or discount_is_percent_fmt:
        if discount_is_percent_fmt:
            n = discount_cell["value"] * 100
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

    # 4. "NxM" escrito en la columna de PRECIO en vez de la de multiplicador.
    #    La guía llama a esto "Compra Múltiple (por cantidad)" —"3x2 Electrolit"—
    #    y es natural escribirlo del lado del precio. Sin esta rama, el paso 5
    #    le saca los dígitos y "3x2" termina dibujado como "$32": una promo
    #    distinta, y sin ningún aviso.
    if price_text:
        quantity_en_precio = parse_quantity_value(price_text)
        if quantity_en_precio:
            return {**quantity_en_precio, "color": price_color}

    # 5. Solo precio
    if price_text:
        return {**parse_price_only(price_text), "color": price_color}

    return None


# ─── Construcción de fields[] ────────────────────────────────────────────────
def extract_fields_from_row(headers, cells) -> list:
    """Procesa UNA fila y devuelve la lista de fields.

    Espejo exacto de promo_excel.extract_fields_from_row: mismos campos
    sintéticos, mismas señales de activación, mismo orden de aparición. Ver el
    original para el detalle de por qué cada variante existe.
    """
    fields = []
    cells_by_normalized_header = {}
    for header, cell in zip(headers, cells):
        if not header:
            continue
        value = cell.get("value")
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
            runs = cell_to_runs(cell)
            field["runs"] = runs
            field["value"] = "".join(r["text"] for r in runs)
        fields.append(field)
        cells_by_normalized_header[normalize(header)] = cell

    multiplier_cell = cells_by_normalized_header.get(normalize(MULTIPLIER_COLUMN_NAME))
    price_cell = cells_by_normalized_header.get(normalize(PRICE_COLUMN_NAME))
    discount_cell = cells_by_normalized_header.get(normalize(DISCOUNT_COLUMN_NAME))
    multiplier2_cell = cells_by_normalized_header.get(normalize(MULTIPLIER2_COLUMN_NAME))
    price2_cell = cells_by_normalized_header.get(normalize(PRICE2_COLUMN_NAME))
    price_data = compute_pesos_field(multiplier_cell, price_cell, discount_cell,
                                     multiplier2_cell, price2_cell)

    # Conector "o"/"ó" suelto en DESCRIPCION: solo activa la variante con
    # conector del modo doble; el texto de la descripción no se toca.
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
            "value": json.dumps(price_data, ensure_ascii=False),
            "runs": [],
            "price": price_data,
        })

    # Variante "promo doble descripción" (activada por DESCRIPCION 2).
    descripcion2_cell = cells_by_normalized_header.get(normalize(DESCRIPCION2_COLUMN_NAME))
    descripcion2_text = cell_text(descripcion2_cell)
    if descripcion2_text:
        has_price = bool(cell_text(price_cell))
        doble_desc_data = {"case": 1 if has_price else 2, "suppress_callout": has_price}
        if has_price:
            price_only = parse_price_only(cell_text(price_cell))
            doble_desc_data["price_integer"] = price_only["integer"][:2]
            doble_desc_data["price_color"] = cell_color(price_cell)

        desc2_runs = cell_to_runs(descripcion2_cell)
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
        # Variante "y llévate el segundo por" (activada por la frase en
        # DESCRIPCION). Acá SÍ se quita la frase disparadora del texto.
        descripcion_field = next(
            (f for f in fields if f["column_normalized"] == normalize("DESCRIPCION")), None
        )
        if descripcion_field and SEGUNDO_POR_TRIGGER_PATTERN.search(descripcion_field.get("value", "")):
            has_price = bool(cell_text(price_cell))
            has_discount = bool(cell_text(discount_cell)) or cell_is_percent(discount_cell)
            if has_price:
                price_only = parse_price_only(cell_text(price_cell))
                segundo_por_data = {
                    "case": 1,
                    "price_integer": price_only["integer"][:2],
                    "price_color": cell_color(price_cell),
                }
            elif has_discount:
                if cell_is_percent(discount_cell):
                    n = discount_cell["value"] * 100
                    discount_number = str(int(n)) if n == int(n) else f"{n:.2f}"
                else:
                    raw = cell_text(discount_cell)
                    m = re.match(r"^(\d+)", raw)
                    discount_number = m.group(1) if m else re.sub(r"\D", "", raw)
                segundo_por_data = {
                    "case": 2,
                    "percent_number": discount_number[:2],
                    "price_color": cell_color(discount_cell),
                }
            else:
                segundo_por_data = None

            if segundo_por_data:
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

    # Las 2 variantes más simples, activadas por EXCLUSIÓN.
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

    # Alcohol: la guía prohíbe "Compra", "Llévate" y "Gratis" en estas promos.
    # Se marca acá, junto al dato, y el motor decide qué puede dibujar. Va como
    # campo y no como una decisión tomada de antemano porque la misma señal la
    # necesitan varias mecánicas, cada una con su alternativa permitida.
    if looks_like_alcohol(*[
        cell_text(cells_by_normalized_header.get(normalize(c))) for c in ALCOHOL_COLUMNS
    ]):
        fields.append({
            "column": ALCOHOL_OUTPUT_FIELD,
            "column_normalized": normalize(ALCOHOL_OUTPUT_FIELD),
            "type": "text",
            "value": "true",
            "runs": [],
            "alcohol": True,
        })

    return fields


def fields_for_table(table) -> list:
    """Todas las filas de una Table -> [{row_number, fields}]."""
    headers = table["headers"]
    return [
        {"row_number": i, "fields": extract_fields_from_row(headers, row)}
        for i, row in enumerate(table["rows"], start=1)
    ]
