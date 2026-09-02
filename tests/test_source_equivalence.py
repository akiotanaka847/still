"""Equivalencia entre el módulo actual y el experimental de ingesta libre.

Esta es la verificación central del plan. El módulo experimental produce
`fields[]` por un camino nuevo (Excel -> Table neutra -> promo_fields) que no
toca openpyxl más que en el adaptador. Si ese camino da EXACTAMENTE el mismo
`fields[]` que el camino original para las 128 filas del fixture, entonces el
motor de render —y con él los 17 modos de promoción y los 3 formatos de
export— no puede notar la diferencia.

Sin este test, "es agnóstico de la fuente" sería una afirmación de diseño. Con
él, es un hecho verificado sobre datos reales.
"""
import json
from pathlib import Path

import pytest

import promo_fields
import promo_source
import promo_source_excel  # noqa: F401 — registra el lector .xlsx al importarse

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = Path(__file__).parent / "golden"
CASES_XLSX = FIXTURES / "casos_prueba_con_subcasos.xlsx"


@pytest.fixture(scope="session")
def golden_fields():
    return json.loads((GOLDEN / "fields_casos_prueba.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def table_from_excel():
    return promo_source.read_table(CASES_XLSX.read_bytes(), "casos.xlsx")


def _diff_rows(expected, actual, etiqueta):
    """Diff legible: qué fila y qué campo difieren, no un volcado de 128 filas."""
    diffs = []
    if len(expected) != len(actual):
        diffs.append(f"  cantidad de filas: {len(expected)} -> {len(actual)}")
    for exp, act in zip(expected, actual):
        if exp == act:
            continue
        exp_by = {f["column"]: f for f in exp["fields"]}
        act_by = {f["column"]: f for f in act["fields"]}
        for col in sorted(set(exp_by) | set(act_by)):
            e, a = exp_by.get(col), act_by.get(col)
            if e != a:
                diffs.append(f"  fila {exp['row_number']}, campo {col!r}")
    if diffs:
        pytest.fail(
            f"{etiqueta} difiere del golden:\n" + "\n".join(diffs[:40])
            + (f"\n  ... y {len(diffs) - 40} más" if len(diffs) > 40 else "")
        )


def test_excel_por_el_camino_nuevo_da_el_mismo_resultado(golden_fields, table_from_excel):
    """Excel -> Table -> promo_fields debe igualar al camino original."""
    actual = promo_fields.fields_for_table(table_from_excel)
    _diff_rows(golden_fields, actual, "El camino Excel->Table->fields")


def _table_to_json_doc(table):
    """Serializa una Table al contrato JSON público del módulo experimental.

    Usa el nivel 2 (runs explícitos) porque es el que tiene que ser capaz de
    expresar TODO lo que expresa un Excel, incluido el color por palabra.
    """
    rows = []
    for row in table["rows"]:
        obj = {}
        for header, cell in zip(table["headers"], row):
            if not header:
                continue
            entry = {"value": cell["value"]}
            if cell["color"]:
                entry["color"] = cell["color"]
            if cell["is_percent"]:
                entry["percent"] = True
            if cell["runs"]:
                entry["runs"] = cell["runs"]
            obj[header] = entry
        rows.append(obj)
    return {"name": table["name"], "rows": rows}


def test_roundtrip_json_conserva_todo(golden_fields, table_from_excel):
    """Excel -> JSON -> Table -> fields[] debe seguir igualando al golden.

    Es la prueba de que el contrato JSON es expresivo de verdad: si algo del
    Excel no se puede escribir en JSON, esta ida y vuelta lo pierde y el diff
    lo delata.
    """
    import promo_source_json  # noqa: F401 — registra el lector .json

    doc = _table_to_json_doc(table_from_excel)
    raw = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    table_json = promo_source.read_table(raw, "casos.json")
    actual = promo_fields.fields_for_table(table_json)
    _diff_rows(golden_fields, actual, "El round-trip Excel->JSON->fields")


def test_json_nivel_1_marcas_en_el_texto():
    """El nivel 1 (string plano con marcas) produce los runs esperados."""
    import promo_source_json  # noqa: F401

    doc = {"rows": [{"DESCRIPCION": "Lleva *2* por ~$50~ ^c/u^", "PESOS O PRECIO": "45"}]}
    table = promo_source.read_table(json.dumps(doc).encode("utf-8"), "x.json")
    fields = promo_fields.extract_fields_from_row(table["headers"], table["rows"][0])

    desc = next(f for f in fields if f["column"] == "DESCRIPCION")
    assert desc["value"] == "Lleva 2 por $50 c/u"
    assert [r["text"] for r in desc["runs"] if r["bold"]] == ["2"]
    assert [r["text"] for r in desc["runs"] if r["strike"]] == ["$50"]
    assert [r["text"] for r in desc["runs"] if r["superscript"]] == ["c/u"]


def test_json_nivel_2_color_por_palabra():
    """El nivel 2 permite color por palabra, que el nivel 1 no puede expresar."""
    import promo_source_json  # noqa: F401

    doc = {"rows": [{
        "DESCRIPCION": {"runs": [
            {"text": "Lleva "},
            {"text": "3", "bold": True, "color": "rgb(220,39,38)"},
        ]},
        "PESOS O PRECIO": "45",
    }]}
    table = promo_source.read_table(json.dumps(doc).encode("utf-8"), "x.json")
    fields = promo_fields.extract_fields_from_row(table["headers"], table["rows"][0])

    desc = next(f for f in fields if f["column"] == "DESCRIPCION")
    assert desc["value"] == "Lleva 3"
    coloreados = [r for r in desc["runs"] if r["color"] == "rgb(220,39,38)"]
    assert [r["text"] for r in coloreados] == ["3"]


def test_json_porcentaje_explicito():
    """`percent: true` reemplaza al number_format de Excel.

    Es la única diferencia semántica real entre orígenes: en Excel un 25% se
    guarda como 0.25 con formato de porcentaje; en JSON hay que declararlo.
    """
    import promo_source_json  # noqa: F401

    doc = {"rows": [{"DESCUENTO": {"value": 0.25, "percent": True}}]}
    table = promo_source.read_table(json.dumps(doc).encode("utf-8"), "x.json")
    fields = promo_fields.extract_fields_from_row(table["headers"], table["rows"][0])

    pesos = next(f for f in fields if f["column"] == "PESOS")
    assert pesos["price"]["mode"] == "percent"
    assert pesos["price"]["number"] == "25"


def test_los_numeros_de_precio_siguen_siendo_strings():
    """`price.integer` debe ser str, no int.

    promo.js decide el layout del modo combinado con `integer.length >= 3`. Si
    un adaptador dejara pasar un número crudo, el layout cambiaría en silencio.
    """
    import promo_source_json  # noqa: F401

    doc = {"rows": [{"MULTIPLICADOR": 2, "PESOS O PRECIO": 150}]}
    table = promo_source.read_table(json.dumps(doc).encode("utf-8"), "x.json")
    fields = promo_fields.extract_fields_from_row(table["headers"], table["rows"][0])

    price = next(f for f in fields if f["column"] == "PESOS")["price"]
    assert isinstance(price["integer"], str), f"integer debe ser str, es {type(price['integer'])}"
    assert isinstance(price.get("multiplier", ""), str)


def test_rich_text_de_excel_produce_runs():
    """Cubre el rich text de Excel (formato por palabra dentro de una celda).

    Hueco encontrado al validar la red: el fixture de 128 casos no tiene NI UNA
    celda con rich text, así que esa rama del adaptador no la ejercitaba nadie.
    Se prueba directo contra openpyxl en memoria en vez de sumar otro .xlsx al
    repo.
    """
    import io

    import openpyxl
    from openpyxl.cell.rich_text import CellRichText, TextBlock
    from openpyxl.cell.text import InlineFont

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["DESCRIPCION", "PESOS O PRECIO"])
    ws.append([
        CellRichText([
            TextBlock(InlineFont(), "Lleva "),
            TextBlock(InlineFont(b=True), "3"),
            TextBlock(InlineFont(), " unidades"),
        ]),
        "45",
    ])
    buf = io.BytesIO()
    wb.save(buf)

    table = promo_source.read_table(buf.getvalue(), "rich.xlsx")
    fields = promo_fields.extract_fields_from_row(table["headers"], table["rows"][0])

    desc = next(f for f in fields if f["column"] == "DESCRIPCION")
    assert desc["value"] == "Lleva 3 unidades"
    assert [r["text"] for r in desc["runs"] if r["bold"]] == ["3"], (
        f"la negrilla por palabra no sobrevivió: {desc['runs']}"
    )
