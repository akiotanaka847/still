"""El HTML que exporta Still se puede volver a abrir y seguir trabajando.

Antes era solo una vista: se veia la pieza, pero para editarla habia que volver
al paquete. Ahora el documento lleva dentro la ficha de la plantilla —el mismo
paquete de intercambio, pero SIN los pixeles— y cada capa dibujada con su
data-id. Los pixeles viven una sola vez, en el src de cada <img>, y quien lea el
archivo (el Visor) vuelve a juntar las dos mitades cruzandolas por ese id.

Lo que protegen estos tests es justo esa economia: que la ficha este, que se
pueda leer, y que NO duplique los pixeles —que es lo que haria que un HTML de
600 KB pasara a pesar 1,2 MB sin ganar nada—.
"""
import json
import pathlib
import re

import pytest

import convertidor


@pytest.fixture(scope="module")
def html():
    estructura = {
        "archivo": "pieza.psd",
        "lienzo": {"ancho": 1000, "alto": 500, "modo": "CMYK", "dpi": 300.0},
        "escala_render": 0.5,
        "asistido_por_ia": True,
        "variantes": [],
        "capas": [
            {"id": "c1", "nombre": "FONDO", "nombre_limpio": "fondo_general",
             "clase": "pixel", "rol": "fondo", "confianza": 0.9, "visible": True,
             "editable": False, "caja": {"x": 0, "y": 0, "w": 1000, "h": 500}},
            {"id": "c2", "nombre": "TEXT_PRECIO", "nombre_limpio": "precio_promo",
             "clase": "type", "rol": "precio", "confianza": 0.8, "visible": True,
             "editable": True, "caja": {"x": 10, "y": 20, "w": 200, "h": 80},
             "texto": "38", "fuente": "Work Sans", "tamano": 60.0,
             "color": "rgb(0,0,0)"},
        ],
    }
    rasters = {"c1": "QUJD", "c2": "REVG"}       # base64 de juguete
    return convertidor.a_html(estructura, rasters, "pieza.psd")


def _ficha(html):
    m = re.search(
        r'<script type="application/json" id="still-plantilla">(.*?)</script>',
        html, re.S)
    assert m, "el HTML no lleva la ficha de la plantilla"
    return json.loads(m.group(1))


def test_el_html_lleva_la_ficha_y_es_json_valido(html):
    f = _ficha(html)
    assert f["formato"] == convertidor.FORMATO
    assert f["version"] == convertidor.FORMATO_VERSION


def test_la_ficha_trae_lo_que_el_dibujo_no_dice(html):
    """Roles, editables y resolucion: nada de eso se deduce mirando los pixeles."""
    f = _ficha(html)
    assert f["lienzo"] == {"ancho": 1000, "alto": 500, "dpi": 300.0}
    roles = {c["id"]: c["rol"] for c in f["capas"]}
    assert roles == {"c1": "fondo", "c2": "precio"}
    assert [c["id"] for c in f["capas"] if c["editable"]] == ["c2"]


def test_la_ficha_conserva_el_orden_de_pintado(html):
    """Es el unico dato que mantiene la pieza identica al PSD."""
    assert [c["id"] for c in _ficha(html)["capas"]] == ["c1", "c2"]


def test_la_ficha_no_duplica_los_pixeles(html):
    """Van una sola vez, en el src de cada <img>."""
    assert all("pixeles" not in c for c in _ficha(html)["capas"])
    assert html.count("QUJD") == 1


def test_cada_capa_dibujada_lleva_su_id(html):
    """Sin data-id no hay forma de volver a juntar ficha y pixeles."""
    ids = set(re.findall(r'<img class="capa[^>]*?data-id="([^"]+)"', html))
    assert {"c1", "c2"} <= ids


def test_el_texto_de_una_capa_no_puede_cortar_el_script():
    """Una capa que diga "</script>" romperia el documento entero."""
    estructura = {
        "archivo": "x.psd",
        "lienzo": {"ancho": 10, "alto": 10, "modo": "RGB", "dpi": None},
        "escala_render": 1.0, "asistido_por_ia": False, "variantes": [],
        "capas": [{"id": "c1", "nombre": "T", "nombre_limpio": "titulo",
                   "clase": "type", "rol": "promo", "confianza": 0.5,
                   "visible": True, "editable": True,
                   "caja": {"x": 0, "y": 0, "w": 10, "h": 10},
                   "texto": "cierra </script> aqui", "fuente": None,
                   "tamano": None, "color": None}],
    }
    html = convertidor.a_html(estructura, {}, "x.psd")
    # Un solo cierre: el de la propia etiqueta.
    assert html.count("</script>") == html.count("<script")
    assert _ficha(html)["capas"][0]["texto"] == "cierra </script> aqui"


PSD = (pathlib.Path(__file__).resolve().parents[2]
       / "PSDs Ejemplos" / "Cabecera 5 Central B.psd")


@pytest.mark.skipif(not PSD.exists(), reason=f"no esta {PSD.name}")
def test_el_html_del_psd_real_se_puede_releer():
    """Control sobre el archivo del cliente, no solo sobre uno de laboratorio.

    Se analiza el PSD en el momento en vez de leer una conversion guardada:
    `sessions/` es material de paso que la retencion limpia, y apuntar ahi
    dejaba este test saltandose solo sin que nadie se enterara.
    """
    estructura = convertidor.analizar(PSD)
    for c in estructura["capas"]:
        c.setdefault("nombre_limpio", c["nombre"])
        c.setdefault("rol", "decorativo")
        c.setdefault("confianza", 0.0)
    f = _ficha(convertidor.a_html(estructura, {}, PSD.name))
    assert f["lienzo"]["dpi"] == 300.0
    assert len(f["capas"]) > 20
    assert all("rol" in c and "caja" in c and "pixeles" not in c for c in f["capas"])
