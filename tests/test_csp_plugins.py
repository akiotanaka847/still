"""La CSP deja entrar a los plugins, y a nadie mas.

Hay dos clases de plugin y cada una cuesta una excepcion distinta:

- De TERCEROS (Visor, Promos): viven en otro dominio, asi que hay que abrir
  `frame-src`. Es la unica excepcion a `default-src 'self'` en la politica.
- PROPIOS (PDF): viven en /static/plugins/, dentro del propio Still. Ahi
  `frame-src 'self'` ya vale, pero `frame-ancestors 'none'` nos impedia
  incrustarnos a nosotros mismos —el navegador bloquea el marco aunque el
  padre sea del mismo origen—, asi que esa carpeta lleva `'self'`.

Lo que estos tests protegen es que las dos excepciones sigan siendo
excepciones: origenes listados uno por uno y no con un comodin, la apertura de
marco encerrada en esa carpeta, y el resto de la politica sin aflojar.
"""
import pathlib
import re

from fastapi.testclient import TestClient

import main

CSP = main._CSP


def _directiva(nombre: str) -> str:
    for parte in CSP.split(";"):
        parte = parte.strip()
        if parte.startswith(nombre + " ") or parte == nombre:
            return parte
    return ""


def test_los_plugins_de_terceros_pueden_incrustarse():
    fs = _directiva("frame-src")
    assert fs, "sin frame-src los plugins no cargan dentro de Still"
    assert "automatizador-plantillas-production.up.railway.app" in fs
    assert "legendary-pie-3f4d8e.netlify.app" in fs


def test_frame_src_no_lleva_comodines():
    """Un frame-src con * o https: dejaria incrustar cualquier cosa. La lista
    tiene que ser explicita: cada dominio, uno por uno.

    El numero exacto tambien importa: son DOS porque los plugins propios no
    entran aqui. Si alguien mete uno propio en la lista, este test lo canta."""
    fs = _directiva("frame-src")
    for comodin in ("*", "https:", "http:", "data:"):
        assert comodin not in fs.split(), f"frame-src acepta el comodin {comodin!r}: {fs}"
    # 'self' mas exactamente dos origenes
    origenes = [t for t in fs.split()[1:] if t.startswith("http")]
    assert len(origenes) == 2, f"se esperaban 2 origenes, hay {len(origenes)}: {origenes}"
    for o in origenes:
        assert o.startswith("https://"), f"origen sin TLS: {o}"


def test_el_resto_de_la_politica_sigue_cerrada():
    """Abrir frame-src no puede haber aflojado nada mas."""
    assert _directiva("default-src") == "default-src 'self'"
    assert _directiva("connect-src") == "connect-src 'self'"
    assert _directiva("base-uri") == "base-uri 'self'"
    assert _directiva("form-action") == "form-action 'self'"


def test_still_sigue_sin_poder_ser_incrustada():
    """Nosotros incrustamos a otros; que nadie nos incruste a nosotros. Es la
    defensa contra clickjacking y no la toca el cambio de los plugins."""
    assert _directiva("frame-ancestors") == "frame-ancestors 'none'"


def test_los_iframes_no_pueden_llevarse_la_pestana():
    """El sandbox de cada iframe concede lo justo. Sin
    allow-top-navigation, el sitio incrustado no puede navegar la pestana
    entera de Still a otra parte."""
    import pathlib

    html = (pathlib.Path(__file__).resolve().parents[1] / "static" / "index.html").read_text()
    sandboxes = re.findall(r'<iframe[^>]*\bsandbox="([^"]*)"', html)
    assert len(sandboxes) >= 2, f"se esperaban al menos 2 iframes con sandbox, hay {len(sandboxes)}"
    for sb in sandboxes:
        assert "allow-top-navigation" not in sb, f"un iframe puede llevarse la pestana: {sb}"
        assert "allow-scripts" in sb, "sin scripts el plugin no funciona"


def test_los_plugins_no_se_cargan_hasta_abrirlos():
    """El src vive en data-src: traer dos aplicaciones de terceros en cada
    carga de Still seria caro y casi siempre inutil."""
    import pathlib

    html = (pathlib.Path(__file__).resolve().parents[1] / "static" / "index.html").read_text()
    for marco in re.findall(r"<iframe[^>]*id=\"frame-[^\"]+\"[^>]*>", html):
        assert "data-src=" in marco, f"iframe sin carga perezosa: {marco[:90]}"
        assert not re.search(r'\ssrc="', marco), f"iframe con src fijo: {marco[:90]}"


# ─── Plugins propios: los que sirve el propio Still ──────────────────────────
def _cliente() -> TestClient:
    return TestClient(main.app)


HTML = (pathlib.Path(__file__).resolve().parents[1] / "static" / "index.html").read_text()


def test_el_plugin_pdf_se_sirve_desde_still():
    r = _cliente().get("/static/plugins/pdf.html")
    assert r.status_code == 200, "el plugin PDF no se sirve"
    assert "text/html" in r.headers.get("content-type", "")


def test_still_puede_incrustar_su_propio_plugin():
    """Sin esto el marco no carga y el plugin queda en blanco: se comprobo en
    el navegador antes de existir el arreglo."""
    r = _cliente().get("/static/plugins/pdf.html")
    csp = r.headers["content-security-policy"]
    assert "frame-ancestors 'self'" in csp, f"la carpeta de plugins sigue cerrada: {csp}"
    assert r.headers["x-frame-options"] == "SAMEORIGIN"


def test_la_apertura_de_marco_no_sale_de_esa_carpeta():
    """Solo /static/plugins/ se puede incrustar. Todo lo demas —la app, el
    CSS, la API— sigue sin poder incrustarse en ninguna parte."""
    c = _cliente()
    for ruta in ("/", "/static/css/tailwind.css", "/api/status"):
        r = c.get(ruta)
        csp = r.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in csp, f"{ruta} se puede incrustar: {csp}"
        assert r.headers["x-frame-options"] == "DENY", ruta


def test_el_plugin_propio_no_abre_ningun_origen_nuevo():
    """La ventaja de servirlo nosotros es justamente esa: cero dominios
    nuevos en la politica."""
    assert "pdf" not in main.PLUGINS_PERMITIDOS
    assert 'data-src="/static/plugins/pdf.html"' in HTML, "el iframe no apunta a una ruta propia"


def test_el_plugin_pdf_esta_cableado_en_la_interfaz():
    """Cuatro sitios: menu, tarjeta del Home, panel y conmutador de pestanas.
    Si falta uno, el plugin existe pero no hay como llegar a el."""
    for pieza in ('id="nav-pdf"', 'id="panel-pdf"', 'id="frame-pdf"',
                  "switchTab('pdf')", "'visor', 'promos', 'pdf'",
                  "['nav-pdf', 'pdf']", "name === 'pdf'"):
        assert pieza in HTML, f"falta el cableado: {pieza}"


def test_las_columnas_del_home_siguen_al_numero_de_plugins():
    """Declarar mas columnas que tarjetas deja un hueco a la derecha; ya
    hubo que arreglarlo una vez."""
    tarjetas = len(re.findall(r"switchTab\('(?:visor|promos|pdf)'\)\"\n\s+class=\"group", HTML))
    assert tarjetas == 3, f"se esperaban 3 tarjetas de plugin, hay {tarjetas}"
    assert "grid md:grid-cols-3 gap-4" in HTML, "el grid de plugins no tiene 3 columnas"
