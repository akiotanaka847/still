"""Adaptador Excel -> Table (contrato neutro).

Acá vive TODO lo que es exclusivo de Excel: openpyxl, los colores de tema, el
rich text por bloques, las filas y columnas ocultas. Nada de esto cruza la
frontera: lo que sale es una Table con celdas neutras (ver promo_source.py).

Reusa las funciones de promo_excel en vez de reimplementarlas — en particular
`parse_theme_colors` y `_color_from_openpyxl`, que resuelven el color de fuente
(incluidos los colores de TEMA con tint, que es el caso más común en la
práctica) antes de que el dato salga de este módulo.
"""
import io

import openpyxl
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.utils import get_column_letter

from promo_excel import (
    _color_from_openpyxl,
    apply_auto_superscript,
    parse_markup_runs,
    parse_theme_colors,
)
from promo_source import PromoSourceError, make_cell, make_table, register_reader


def _rich_text_to_runs(value, cell_color):
    """Runs de una celda con rich text de Excel (formato por bloque).

    Mismo criterio que promo_excel.cell_to_runs: cada TextBlock aporta sus
    banderas, y el texto de cada bloque pasa igual por parse_markup_runs para
    soportar las marcas dentro de un bloque ya formateado.
    """
    runs = []
    for block in value:
        if isinstance(block, TextBlock):
            font = block.font
            block_color = _color_from_openpyxl(getattr(font, "color", None), None) or cell_color
            for r in parse_markup_runs(str(block.text)):
                runs.append({
                    **r,
                    "bold": r["bold"] or bool(getattr(font, "b", False)),
                    "italic": r["italic"] or bool(getattr(font, "i", False)),
                    "strike": r["strike"] or bool(getattr(font, "strike", False)),
                    "superscript": r["superscript"] or getattr(font, "vertAlign", None) == "superscript",
                    "subscript": r["subscript"] or getattr(font, "vertAlign", None) == "subscript",
                    "color": r["color"] or block_color,
                })
        else:
            for r in parse_markup_runs(str(block)):
                runs.append({**r, "color": r["color"] or cell_color})
    return runs


def read_excel(content: bytes):
    """Lee un .xlsx/.xlsm y devuelve la Table neutra.

    Mismas reglas que el módulo original: la primera fila visible son los
    headers, las filas y columnas ocultas se ignoran por completo, y se toma
    cualquier fila visible con al menos un valor.
    """
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, rich_text=True)
    except Exception as e:
        raise PromoSourceError(f"No se pudo leer el Excel: {e}")

    theme_colors = parse_theme_colors(getattr(wb, "loaded_theme", None))
    ws = wb.active

    def is_row_hidden(idx):
        dim = ws.row_dimensions.get(idx)
        return bool(dim and dim.hidden)

    def is_col_hidden(idx):
        dim = ws.column_dimensions.get(get_column_letter(idx))
        return bool(dim and dim.hidden)

    visible_rows = [row for i, row in enumerate(ws.iter_rows(), start=1) if not is_row_hidden(i)]
    if len(visible_rows) < 2:
        raise PromoSourceError(
            "El Excel no tiene datos visibles (se esperaba fila de headers + al "
            "menos 1 fila de promoción, sin contar filas/columnas ocultas)"
        )

    def visible_cells(row):
        return [c for c in row if not is_col_hidden(c.column)]

    header_cells = visible_cells(visible_rows[0])
    headers = ["" if c.value is None else str(c.value).strip() for c in header_cells]

    rows = []
    for row in visible_rows[1:]:
        cells = visible_cells(row)
        if not any(c.value is not None and str(c.value).strip() != "" for c in cells):
            continue
        rows.append([_to_cell(c, theme_colors) for c in cells])

    return make_table(ws.title, headers, rows)


def _to_cell(cell, theme_colors):
    """Celda de openpyxl -> Cell neutra. Es la frontera: acá muere openpyxl."""
    color = _color_from_openpyxl(cell.font.color, theme_colors) if cell.font else None
    runs = None
    if isinstance(cell.value, CellRichText):
        # Solo se materializan los runs cuando el origen los trae de verdad; si
        # no, se dejan en None para que promo_fields los derive del texto (mismo
        # camino que una celda plana).
        runs = apply_auto_superscript(_rich_text_to_runs(cell.value, color))
    value = cell.value
    if isinstance(value, CellRichText):
        value = str(value)
    return make_cell(
        value=value,
        color=color,
        is_percent=bool(cell.number_format and "%" in cell.number_format),
        runs=runs,
    )


register_reader(["xlsx", "xlsm"], read_excel)
