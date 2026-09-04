"""La tipografia de marca tiene que estar completa y servirse desde aqui.

Las fuentes son Goldplay y Goldplay Alt, del Q.Strauss Design System, y sus
tokens reparten asi:

    --font-display  "Goldplay Alt"   titulares, logotipos
    --font-body     "Goldplay"       texto corrido

Se sirven desde /static/fonts y NO desde un CDN: son fuentes con licencia.
Se convierten con tools/fuentes.py, que pasa de OTF a WOFF2 y recorta los
caracteres (519 KB -> 130 KB).

Lo que estos tests protegen es lo que se rompe en silencio: que falte un
corte, que se cuele Google Fonts otra vez, y sobre todo que el recorte se
lleve por delante la i SIN PUNTO del logotipo.
"""
import pathlib
import re

from fastapi.testclient import TestClient

import main

RAIZ = pathlib.Path(__file__).resolve().parent.parent
FUENTES = RAIZ / "static" / "fonts"
HTML = (RAIZ / "static" / "index.html").read_text()

CORTES = [
    "goldplay-400.woff2",
    "goldplay-500.woff2",
    "goldplay-600.woff2",
    "goldplay-700.woff2",
    "goldplay-alt-700.woff2",
    "goldplay-alt-900.woff2",
]


def test_estan_los_seis_cortes():
    for nombre in CORTES:
        f = FUENTES / nombre
        assert f.exists(), f"falta {nombre} — corre tools/fuentes.py"
        assert f.stat().st_size > 5000, f"{nombre} parece vacio"


def test_el_html_declara_los_cortes_que_existen():
    declarados = set(re.findall(r"/static/fonts/([a-z0-9\-]+\.woff2)", HTML))
    assert declarados, "el HTML no declara ninguna fuente"
    for nombre in declarados:
        assert (FUENTES / nombre).exists(), f"declarado pero no existe: {nombre}"


def test_no_se_cargan_fuentes_desde_un_cdn():
    """Son fuentes con licencia: se sirven desde aqui, no desde Google."""
    assert "fonts.googleapis" not in HTML, "ha vuelto Google Fonts"
    assert "fonts.gstatic" not in HTML


def test_el_reparto_display_y_cuerpo_es_el_del_design_system():
    assert "--font-display: 'Goldplay Alt'" in HTML
    assert "--font-body: 'Goldplay'" in HTML
    # Los titulares tienen que tomar el display, o la marca no se ve.
    assert re.search(r"h1,\s*h2,\s*h3", HTML), "los titulares no piden el display"


def test_la_i_sin_punto_sobrevive_al_recorte():
    """El logotipo escribe "Stıll" con U+0131. Si el recorte se la lleva, el
    navegador la saca de la fuente de sistema y la palabra queda descuadrada
    — y no hay error en ninguna consola que lo delate."""
    from fontTools.ttLib import TTFont

    assert "ı" in HTML, 'el logotipo ya no usa la i sin punto'
    for nombre in CORTES:
        f = TTFont(str(FUENTES / nombre))
        cps = set()
        for t in f["cmap"].tables:
            cps |= set(t.cmap.keys())
        assert 0x0131 in cps, f"{nombre} perdio la i sin punto (U+0131)"


def test_el_espanol_entero_cabe_en_el_recorte():
    """La interfaz va en ingles, pero los nombres de producto llevan acentos."""
    from fontTools.ttLib import TTFont

    f = TTFont(str(FUENTES / "goldplay-400.woff2"))
    cps = set()
    for t in f["cmap"].tables:
        cps |= set(t.cmap.keys())
    for ch in "áéíóúüñÁÉÍÓÚÑ¿¡":
        assert ord(ch) in cps, f"el recorte se llevo {ch!r}"


def test_no_quedan_flechas_escritas_como_texto():
    """Goldplay no trae flechas, ni la redonda ni la Alt. Una flecha escrita
    como texto cae a la fuente de sistema y se nota al lado del resto, asi
    que en la interfaz van como SVG."""
    visible = re.sub(r"<!--.*?-->", "", HTML, flags=re.S)
    visible = re.sub(r"//[^\n]*", "", visible)
    visible = re.sub(r"/\*.*?\*/", "", visible, flags=re.S)
    for flecha in "←→↑↓":
        assert flecha not in visible, f"flecha de texto {flecha!r} en la interfaz"


def test_las_fuentes_se_sirven():
    c = TestClient(main.app)
    for nombre in CORTES:
        r = c.get(f"/static/fonts/{nombre}")
        assert r.status_code == 200, f"{nombre} no se sirve"
        assert len(r.content) > 5000
