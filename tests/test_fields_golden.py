"""Golden test de `fields[]` — la red de seguridad del módulo de Promos.

`fields[]` es el contrato real entre la fuente de datos y el motor de render:
todo lo que está aguas abajo (las ~2700 líneas de static/js/promo.js, los 17
modos de promoción, los 3 formatos de export) consume esa lista y nada más.

Por eso alcanza con congelarla: si `fields[]` sale idéntico, el frontend no
puede notar la diferencia. Esta es la garantía que permite construir el módulo
experimental de ingesta libre (JSON/CSV/API) sin poder romper el actual.

Uso:
    pytest tests/                      # verifica contra el golden
    pytest tests/ --regenerate-golden  # regenera el golden (¡solo a propósito!)

Regenerar solo cuando un cambio de comportamiento sea DELIBERADO, y revisando
el diff del golden a mano — es exactamente el diff que van a ver los usuarios.
"""
import json
from pathlib import Path

import openpyxl
import pytest

import promo_excel

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = Path(__file__).parent / "golden"
CASES_XLSX = FIXTURES / "casos_prueba_con_subcasos.xlsx"


def _fields_for_all_rows(path: Path) -> list:
    """Corre el lector de Excel ACTUAL sobre todas las filas del fixture.

    Usa las funciones reales de promo_excel, no una reimplementación: el
    objetivo es capturar el comportamiento tal cual es hoy, con sus rarezas
    incluidas.
    """
    ws, headers, data_rows, theme_colors = promo_excel.load_workbook_rows(
        path.read_bytes()
    )
    return [
        {
            "row_number": i,
            "fields": promo_excel.extract_fields_from_row(headers, cells, theme_colors),
        }
        for i, cells in enumerate(data_rows, start=1)
    ]


@pytest.fixture(scope="session")
def actual_fields():
    return _fields_for_all_rows(CASES_XLSX)


def test_fixture_tiene_los_casos_esperados(actual_fields):
    """Si el fixture cambia de tamaño, el golden ya no cubre lo que creíamos."""
    assert len(actual_fields) >= 100, (
        f"El fixture debería traer los ~128 casos de prueba, trajo {len(actual_fields)}"
    )


def test_fields_golden(actual_fields, request):
    """El corazón de la red de seguridad: `fields[]` no cambia."""
    golden_path = GOLDEN / "fields_casos_prueba.json"
    actual_json = json.dumps(actual_fields, ensure_ascii=False, indent=2, sort_keys=True)

    if request.config.getoption("--regenerate-golden") or not golden_path.exists():
        golden_path.parent.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(actual_json, encoding="utf-8")
        pytest.skip(f"Golden regenerado en {golden_path.name} — revisá el diff a mano")

    expected_json = golden_path.read_text(encoding="utf-8")
    if actual_json == expected_json:
        return

    # Diff útil: decir QUÉ fila y QUÉ campo cambió, no volcar 128 filas.
    expected = json.loads(expected_json)
    diffs = []
    for exp, act in zip(expected, actual_fields):
        if exp != act:
            exp_by = {f["column"]: f for f in exp["fields"]}
            act_by = {f["column"]: f for f in act["fields"]}
            for col in sorted(set(exp_by) | set(act_by)):
                if exp_by.get(col) != act_by.get(col):
                    diffs.append(f"  fila {exp['row_number']}, campo {col!r}")
    if len(expected) != len(actual_fields):
        diffs.append(f"  cantidad de filas: {len(expected)} -> {len(actual_fields)}")

    pytest.fail(
        "fields[] cambió respecto del golden:\n" + "\n".join(diffs[:40])
        + (f"\n  ... y {len(diffs) - 40} diferencias más" if len(diffs) > 40 else "")
    )


def test_los_17_modos_estan_representados(actual_fields):
    """El golden no sirve de red si el fixture no ejercita los modos reales.

    Verifica que aparezcan los 5 modos de PESOS y las 4 variantes especiales —
    si alguno desaparece, el fixture dejó de cubrir ese caso y el golden estaría
    dando una falsa sensación de seguridad.
    """
    modos_pesos = set()
    sinteticos = set()
    for row in actual_fields:
        for f in row["fields"]:
            if f.get("price"):
                modos_pesos.add(f["price"]["mode"])
            if f["column"].startswith("PROMO_"):
                sinteticos.add(f["column"])

    assert {"price", "percent", "quantity", "combined", "double"} <= modos_pesos, (
        f"Faltan modos de PESOS en el fixture: {modos_pesos}"
    )
    assert {
        "PROMO_DOBLE_DESCRIPCION",
        "PROMO_SEGUNDO_POR",
        "PROMO_SOLO_FRASE_DESC",
        "PROMO_SOLO_DESCRIPCION",
    } <= sinteticos, f"Faltan variantes especiales en el fixture: {sinteticos}"
