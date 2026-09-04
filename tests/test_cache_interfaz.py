"""La interfaz no puede quedarse cacheada tras un despliegue.

Esto existe por un fallo real: despues de subir Still 2.0 a produccion, la
marca seguia apareciendo como un cuadrado con una "S" de texto, que es como
se dibujaba ANTES de tener la imagen del logo. No era el logo: era el
index.html anterior servido desde la cache del navegador.

La causa era que la respuesta de "/" no llevaba ninguna directiva de cache, y
sin directivas el navegador aplica cache heuristica y puede usar su copia sin
preguntar. Estos tests fijan las tres piezas del arreglo.
"""
from fastapi.testclient import TestClient

import main


def _cliente() -> TestClient:
    return TestClient(main.app)


def test_la_pagina_obliga_a_revalidar():
    r = _cliente().get("/")
    assert r.status_code == 200
    cc = r.headers.get("cache-control", "")
    assert "no-cache" in cc, f"'/' sin no-cache: {cc!r}"


def test_el_css_compilado_obliga_a_revalidar():
    """Si el CSS se queda viejo faltan las utilidades nuevas y la pantalla
    sale descuadrada sin que el codigo explique por que."""
    r = _cliente().get("/static/css/tailwind.css")
    assert r.status_code == 200
    cc = r.headers.get("cache-control", "")
    assert "no-cache" in cc, f"el CSS sin no-cache: {cc!r}"


def test_revalidar_sale_gratis():
    """no-cache no es 'no guardes' sino 'pregunta antes de usar': con el ETag
    la respuesta debe ser un 304 sin cuerpo."""
    c = _cliente()
    primera = c.get("/static/css/tailwind.css")
    etag = primera.headers.get("etag")
    assert etag, "sin ETag no hay revalidacion posible"

    segunda = c.get("/static/css/tailwind.css", headers={"If-None-Match": etag})
    assert segunda.status_code == 304
    assert not segunda.content


def test_las_imagenes_si_se_pueden_guardar():
    """Pesan mucho mas y cuando cambian se cambia su nombre, asi que aqui no
    se paga una revalidacion por carga."""
    r = _cliente().get("/static/still-mark.png")
    assert r.status_code == 200
    assert "no-cache" not in r.headers.get("cache-control", "")
