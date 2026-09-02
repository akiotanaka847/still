"""Contrato neutro de entrada para el módulo experimental de Promos.

El módulo de Promos actual solo sabe leer `.xlsx`. Este contrato es la
frontera que permite alimentarlo desde cualquier origen (JSON, CSV, una API)
sin tocar nada de lo que ya funciona.

La frontera NO está en `fields[]` — eso es la salida. Está un paso antes, en
lo que hoy es "una celda de Excel". De una celda solo se necesitan cuatro
cosas, y ninguna es exclusiva de Excel:

    value       el dato crudo, sin interpretar
    color       color del texto YA resuelto a "rgb(R,G,B)" (o None)
    is_percent  si el origen declara que el valor es un porcentaje
    runs        tramos de texto con formato, si el origen los trae explícitos

Todo lo demás de Excel (colores de tema, tint, CellRichText, filas ocultas)
muere en el adaptador y nunca cruza esta frontera.

Este módulo NO importa openpyxl a propósito: es la garantía de que el contrato
es realmente agnóstico. El adaptador de Excel vive aparte, en
promo_source_excel.py.
"""
from typing import Any, Callable, Optional, TypedDict


class Run(TypedDict):
    """Un tramo de texto con formato uniforme.

    Misma estructura que produce promo_excel._run() — las 7 claves siempre
    presentes, porque el render las lee sin defaults (ver lineToSvgText en
    static/js/promo.js).
    """
    text: str
    bold: bool
    italic: bool
    strike: bool
    superscript: bool
    subscript: bool
    color: Optional[str]


class Cell(TypedDict):
    value: Any                      # str | int | float | None, crudo
    color: Optional[str]            # "rgb(R,G,B)" ya resuelto, o None
    is_percent: bool                # el origen declara formato de porcentaje
    runs: Optional[list]            # runs explícitos, o None = derivar del texto


class Table(TypedDict):
    name: str                       # nombre del origen (hoja, archivo, lote)
    headers: list                   # list[str]
    rows: list                      # list[list[Cell]] — PARALELO a headers


class PromoSourceError(ValueError):
    """Origen de datos inválido o sin filas utilizables.

    Excepción propia y no HTTPException: este módulo es agnóstico del
    framework. La capa web la traduce a un 400.
    """


def make_cell(value: Any = None, color: str = None,
              is_percent: bool = False, runs: list = None) -> Cell:
    """Constructor de Cell con los defaults correctos.

    Usarlo siempre en vez de armar el dict a mano: las cuatro claves deben
    existir para que promo_fields no tenga que hacer .get() en todos lados.
    """
    return {"value": value, "color": color,
            "is_percent": bool(is_percent), "runs": runs}


def make_table(name: str, headers: list, rows: list) -> Table:
    """Arma una Table validando la invariante que sostiene todo el diseño.

    Cada fila debe tener EXACTAMENTE tantas celdas como headers: el resto del
    pipeline las recorre con zip(headers, cells), así que una fila corta
    perdería columnas en silencio.
    """
    headers = [("" if h is None else str(h)) for h in headers]
    fixed = []
    for row in rows:
        row = list(row)
        if len(row) < len(headers):
            row += [make_cell() for _ in range(len(headers) - len(row))]
        elif len(row) > len(headers):
            row = row[:len(headers)]
        fixed.append(row)
    return {"name": str(name or ""), "headers": headers, "rows": fixed}


# ─── Registry de lectores por extensión ──────────────────────────────────────
# Los adaptadores se registran a sí mismos al importarse (ver el final de
# promo_source_excel.py / promo_source_json.py). Así agregar un origen nuevo
# es crear un archivo y registrarlo, sin tocar este módulo.
_READERS: dict = {}


def register_reader(extensions, reader: Callable[[bytes], Table]) -> None:
    for ext in extensions:
        _READERS[ext.lower().lstrip(".")] = reader


def supported_extensions() -> list:
    return sorted(_READERS)


def read_table(content: bytes, filename: str) -> Table:
    """Lee cualquier origen soportado y devuelve la Table neutra."""
    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    reader = _READERS.get(ext)
    if not reader:
        soportados = ", ".join(f".{e}" for e in supported_extensions()) or "ninguno"
        raise PromoSourceError(
            f"No sé leer archivos .{ext or '(sin extensión)'}. Soportados: {soportados}"
        )
    table = reader(content)
    if not table["rows"]:
        raise PromoSourceError("El archivo no tiene ninguna fila de datos utilizable")
    return table
