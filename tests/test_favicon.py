"""El favicon tiene que leerse a tamano de pestana.

Esto existe por un reporte concreto: "falta el logo" en la pestana. No
faltaba — el favicon ERA el logo, reducido. El problema es que la "S" del
logo es un trazo con degradado suave sobre un fondo de cuatro colores, y a
16 px pierde el contraste contra su propio fondo: queda una mancha violeta.

La solucion no fue cambiar el fondo (eso costaba la identidad de la marca,
que es justo el cian y el magenta) sino pintar el glifo en blanco pleno en
los tamanos chicos. Ver tools/favicons.py.

Estos tests fijan las dos mitades: que el glifo tenga cuerpo suficiente en
16 px, y que el .ico traiga los tamanos que el navegador va a pedir en vez
de uno grande que reduce mal.
"""
import pathlib

from PIL import Image

RAIZ = pathlib.Path(__file__).resolve().parent.parent
STATIC = RAIZ / "static"

# Medido: la reduccion ingenua del logo deja 29 pixeles casi blancos de 256,
# y con el glifo en blanco pleno son 45. El umbral va entre los dos, mas
# cerca del bueno, para que una regresion al degradado suave lo rompa.
MINIMO_PIXELES_GLIFO = 38


def _pixeles_glifo(ruta: pathlib.Path) -> int:
    """Cuantos pixeles casi blancos quedan al verlo a 16x16."""
    im = Image.open(ruta).convert("RGBA")
    if im.size[0] != 16:
        im = im.resize((16, 16), Image.LANCZOS)
    px = im.load()
    return sum(
        1 for y in range(16) for x in range(16) if min(px[x, y][:3]) >= 200
    )


def test_el_glifo_se_lee_a_16px():
    n = _pixeles_glifo(STATIC / "favicon-16.png")
    assert n >= MINIMO_PIXELES_GLIFO, (
        f"solo {n} pixeles de glifo: la S se pierde a tamano de pestana"
    )


def test_el_glifo_del_logo_grande_no_valdria():
    """Deja constancia de por que hace falta una version aparte: reducir el
    arte original directamente NO pasa el umbral."""
    n = _pixeles_glifo(STATIC / "still-mark.png")
    assert n < MINIMO_PIXELES_GLIFO, (
        "el logo grande ya se lee a 16px; entonces tools/favicons.py sobra"
    )


def test_el_ico_trae_los_tamanos_que_pide_el_navegador():
    """Era un solo 256x256, que los navegadores reducen mal — el origen del
    problema. Tiene que traer 16, 32 y 48."""
    ico = Image.open(STATIC / "favicon.ico")
    tam = {s[0] for s in ico.info.get("sizes", [])}
    assert {16, 32, 48} <= tam, f"al .ico le faltan tamanos: {sorted(tam)}"


def test_existen_todos_los_iconos_declarados():
    import re

    html = (STATIC / "index.html").read_text()
    declarados = re.findall(r'href="/static/(favicon[^"?]*)', html)
    assert declarados, "el HTML no declara ningun icono"
    for nombre in set(declarados):
        assert (STATIC / nombre).exists(), f"declarado pero no existe: {nombre}"


def test_los_iconos_van_versionados():
    """El navegador guarda los favicons mas agresivamente que nada y a veces
    ni mira las cabeceras: sin cambiar la URL se sigue viendo el anterior."""
    import re

    html = (STATIC / "index.html").read_text()
    enlaces = re.findall(r'href="(/static/favicon[^"]*)"', html)
    assert enlaces, "el HTML no declara ningun icono"
    sin_version = [e for e in enlaces if "?v=" not in e]
    assert not sin_version, f"iconos sin ?v=: {sin_version}"


def test_el_icono_se_sirve_en_la_raiz():
    """El navegador pide /favicon.ico por su cuenta, sin mirar los <link>: al
    abrir la pestana, desde un marcador, desde el historial. Devolvia 404, y
    los navegadores cachean ese 404 — la pestana se quedaba sin icono por
    mucho que el HTML declarara los suyos. El ?v= de los <link> no ayuda
    contra una URL que el navegador construye solo.
    """
    from fastapi.testclient import TestClient

    import main

    r = TestClient(main.app).get("/favicon.ico")
    assert r.status_code == 200, "/favicon.ico en la raiz no se sirve"
    assert r.headers["content-type"].startswith("image/")
    assert len(r.content) > 500


def test_los_iconos_de_apple_se_sirven_en_la_raiz():
    """iOS los pide en la raiz al anadir a la pantalla de inicio si no
    encuentra la etiqueta."""
    from fastapi.testclient import TestClient

    c = TestClient(__import__("main").app)
    for ruta in ("/apple-touch-icon.png", "/apple-touch-icon-precomposed.png"):
        r = c.get(ruta)
        assert r.status_code == 200, f"{ruta} no se sirve"
        assert r.headers["content-type"].startswith("image/")


def test_los_iconos_de_raiz_si_se_pueden_guardar():
    """Al contrario que el HTML: son pequenos y cuando cambian se cambia su
    nombre o su ?v= en el HTML."""
    from fastapi.testclient import TestClient

    r = TestClient(__import__("main").app).get("/favicon.ico")
    assert "no-cache" not in r.headers.get("cache-control", "")
