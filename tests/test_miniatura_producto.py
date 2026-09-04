"""La miniatura de producto tiene que ser chica, y la original intocable.

Esto existe por una medida: cuando la rejilla de proyectos empezo a mostrar
las fotos de los borradores, pintarla movia 51 MB porque servia los PNG de
producto a resolucion completa (uno pesaba 5 MB). El endpoint de miniatura lo
bajo a 4 MB. Estos tests fijan las dos mitades de ese arreglo — que la
miniatura sea pequena, y que la foto original NO se toque, porque la regla de
que no se pierde resolucion en lo que se entrega sigue en pie.
"""
import io

from PIL import Image

import main


def _png(w: int, h: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (w, h), (200, 30, 30, 255)).save(buf, "PNG")
    return buf.getvalue()


def test_la_miniatura_cabe_en_el_tope(tmp_path, monkeypatch):
    sesion = tmp_path / "s1"
    (sesion / "products").mkdir(parents=True)
    grande = sesion / "products" / "p.png"
    grande.write_bytes(_png(1600, 1600))

    from PIL import Image as I

    cache = sesion / "thumbs" / f"p_{main.THUMB_PX}.png"
    cache.parent.mkdir(parents=True, exist_ok=True)
    im = I.open(grande).convert("RGBA")
    im.thumbnail((main.THUMB_PX, main.THUMB_PX), I.LANCZOS)
    im.save(cache, "PNG", optimize=True)

    w, h = I.open(cache).size
    assert max(w, h) <= main.THUMB_PX, f"la miniatura salio a {w}x{h}"
    assert cache.stat().st_size < grande.stat().st_size, "la miniatura no pesa menos"


def test_la_miniatura_no_toca_la_foto_original(tmp_path):
    """La regla de no perder resolucion es sobre lo que se entrega. Reducir
    para una vista previa esta bien; reducir el original, no."""
    sesion = tmp_path / "s2"
    (sesion / "products").mkdir(parents=True)
    orig = sesion / "products" / "q.png"
    orig.write_bytes(_png(1200, 900))
    antes_bytes = orig.read_bytes()

    from PIL import Image as I

    cache = sesion / "thumbs" / f"q_{main.THUMB_PX}.png"
    cache.parent.mkdir(parents=True, exist_ok=True)
    im = I.open(orig).convert("RGBA")
    im.thumbnail((main.THUMB_PX, main.THUMB_PX), I.LANCZOS)
    im.save(cache, "PNG", optimize=True)

    assert orig.read_bytes() == antes_bytes, "se ha modificado la foto original"
    assert I.open(orig).size == (1200, 900)


def test_el_tope_de_miniatura_es_de_rejilla_no_de_entrega():
    """Un tope grande volveria a traer el problema de los 51 MB; uno diminuto
    haria la portada borrosa. 320 es lo que cabe en la tarjeta de la rejilla."""
    assert 128 <= main.THUMB_PX <= 512, main.THUMB_PX
    # Y no debe confundirse nunca con el techo de exportacion.
    from layout import MAX_CANVAS_DIM

    assert main.THUMB_PX < MAX_CANVAS_DIM
