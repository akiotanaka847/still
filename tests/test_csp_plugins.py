"""La CSP deja entrar a los plugins, y a nadie mas.

Los plugins (Visor, Promos) son aplicaciones de terceros que se abren DENTRO de
Still, en un iframe. Eso obliga a abrir `frame-src`, que es la unica excepcion
a `default-src 'self'` en toda la politica.

Lo que estos tests protegen es que esa excepcion siga siendo una excepcion:
que los origenes esten listados uno por uno y no con un comodin, y que el
resto de la politica no se haya aflojado de paso.
"""
import re

import main

CSP = main._CSP


def _directiva(nombre: str) -> str:
    for parte in CSP.split(";"):
        parte = parte.strip()
        if parte.startswith(nombre + " ") or parte == nombre:
            return parte
    return ""


def test_los_dos_plugins_pueden_incrustarse():
    fs = _directiva("frame-src")
    assert fs, "sin frame-src los plugins no cargan dentro de Still"
    assert "automatizador-plantillas-production.up.railway.app" in fs
    assert "legendary-pie-3f4d8e.netlify.app" in fs


def test_frame_src_no_lleva_comodines():
    """Un frame-src con * o https: dejaria incrustar cualquier cosa. La lista
    tiene que ser explicita: cada dominio, uno por uno."""
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
