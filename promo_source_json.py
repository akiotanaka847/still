"""Adaptador JSON -> Table (contrato neutro).

Formato aceptado: un objeto con `rows`, donde cada fila es un objeto
{nombre de campo: valor}. Los nombres de campo son los mismos que las columnas
del Excel (CALLOUT, FRASE APOYO, PESOS O PRECIO, ...), y se emparejan igual:
sin distinguir mayúsculas, tildes ni espacios.

Cada valor se puede escribir de dos formas, mezclables en el mismo documento:

  Nivel 1 — string plano, con marcas para el formato:
      "FRASE APOYO": "*LLEVA* 3 por ~$50~ ^c/u^"

  Nivel 2 — objeto, cuando hace falta control fino (color por palabra, o
  declarar un porcentaje):
      "DESCRIPCION": {"runs": [{"text": "Lleva "},
                               {"text": "3", "bold": true, "color": "rgb(220,39,38)"}]}
      "DESCUENTO":   {"value": 0.25, "percent": true}

Sobre `percent`: es la única diferencia semántica real con Excel. Allá un 25%
se guarda como 0.25 con formato de porcentaje, y eso se detecta solo. En JSON
no hay formato de celda, así que o se declara `"percent": true` sobre el valor
numérico, o se manda el texto ya formateado ("25%").
"""
import json

from promo_source import PromoSourceError, make_cell, make_table, register_reader

# Defaults de un run — deben coincidir con promo_excel._run(): el motor de
# render lee w.bold / w.strike / w.superscript sin defaults, así que un run
# incompleto rompería el dibujado.
_RUN_DEFAULTS = {
    "text": "", "bold": False, "italic": False, "strike": False,
    "superscript": False, "subscript": False, "color": None,
}


def coerce_run(d) -> dict:
    """Completa un run parcial del JSON con las 7 claves obligatorias."""
    if not isinstance(d, dict):
        return {**_RUN_DEFAULTS, "text": str(d)}
    run = {**_RUN_DEFAULTS}
    for k in _RUN_DEFAULTS:
        if k in d and d[k] is not None:
            run[k] = str(d[k]) if k in ("text", "color") else bool(d[k])
    return run


def _to_cell(raw):
    """Un valor del JSON (nivel 1 o 2) -> Cell neutra."""
    if raw is None:
        return make_cell()
    if isinstance(raw, dict):
        runs = raw.get("runs")
        runs = [coerce_run(r) for r in runs] if isinstance(runs, list) and runs else None
        value = raw.get("value")
        # Si vienen runs pero no value, el texto se deriva de los runs para que
        # las reglas que miran el texto crudo (imagen vs texto, señales de
        # variante) funcionen igual que con cualquier otro origen.
        if value is None and runs:
            value = "".join(r["text"] for r in runs)
        return make_cell(
            value=value,
            color=raw.get("color"),
            is_percent=bool(raw.get("percent") or raw.get("is_percent")),
            runs=runs,
        )
    return make_cell(value=raw)


def read_json(content: bytes):
    """Lee un .json y devuelve la Table neutra."""
    try:
        doc = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise PromoSourceError(f"No se pudo leer el JSON: {e}")

    # Se acepta tanto {"rows": [...]} como una lista suelta de filas, que es lo
    # que manda naturalmente un sistema externo.
    if isinstance(doc, list):
        rows_raw, name = doc, "promos"
    elif isinstance(doc, dict):
        rows_raw = doc.get("rows")
        name = str(doc.get("name") or "promos")
        if rows_raw is None:
            raise PromoSourceError(
                'El JSON debe traer una clave "rows" con la lista de promociones'
            )
    else:
        raise PromoSourceError("El JSON debe ser un objeto con \"rows\", o una lista de filas")

    if not isinstance(rows_raw, list):
        raise PromoSourceError('"rows" debe ser una lista')

    # Los headers son la unión de las claves de todas las filas, en orden de
    # aparición: así una fila que omite una columna no desalinea al resto.
    headers = []
    for row in rows_raw:
        if not isinstance(row, dict):
            raise PromoSourceError("Cada elemento de \"rows\" debe ser un objeto")
        for k in row:
            if k not in headers:
                headers.append(k)

    rows = [[_to_cell(row.get(h)) for h in headers] for row in rows_raw]
    return make_table(name, headers, rows)


register_reader(["json"], read_json)
