"""La pieza debe degradar con gracia al crecer la cantidad de productos.

Blinda tres cosas que se rompieron de verdad y que un cambio en layout.py o
renderer.py puede volver a romper sin que nada se queje:

  1. La salida nunca pasa de MAX_CANVAS_DIM. Antes el tope existía como
     constante pero solo aplicaba al escalar HACIA ARRIBA, así que 60
     productos salían a 7616x4985 (38 MP).
  2. La pieza no se vuelve una tira. Antes las filas estaban fijas en 3, y
     como el canvas es exacto al contenido, 60 productos daban 4.2:1.
  3. Los sets chicos no cambian de reparto. Es la regresión más fácil de
     introducir al tocar el cálculo de filas.

No necesita imágenes reales: usa productos sintéticos con proporciones de
envase creíbles, así que corre en la suite automática.
"""
import pytest

from layout import compute_layout_any
from renderer import MAX_CANVAS_DIM, _compute_export_scale


def _productos(n, ratio=0.5):
    """n productos con la misma proporción (alto > ancho, como una botella)."""
    h = 1000
    w = int(h * ratio)
    return [{"name": f"sku{i}", "width": w, "height": h,
             "filepath": f"/dev/null/{i}.png"} for i in range(n)]


def _salida(n, **kw):
    layout = compute_layout_any(_productos(n), sort_strategy="auto",
                               base_height=760, **kw)
    escala = _compute_export_scale(layout)
    cw = layout["canvas_width"] * escala
    ch = layout["canvas_height"] * escala
    alto_prod = sum(it["sh"] for it in layout["items"]) / n * escala
    return {"filas": layout["rows_count"], "w": cw, "h": ch,
            "aspecto": cw / ch, "alto_prod": alto_prod}


@pytest.mark.parametrize("n", [1, 2, 6, 14, 20, 30, 40, 60, 100])
def test_la_salida_respeta_el_tope_de_lienzo(n):
    s = _salida(n)
    assert max(s["w"], s["h"]) <= MAX_CANVAS_DIM + 1, (
        f"{n} productos dan {s['w']:.0f}x{s['h']:.0f}, sobre el tope de {MAX_CANVAS_DIM}")


@pytest.mark.parametrize("n", [14, 20, 30, 40, 60, 100])
def test_la_pieza_no_se_vuelve_una_tira(n):
    """Con 3 filas fijas, 60 productos daban 4.2:1 y 100 aún peor."""
    s = _salida(n)
    assert s["aspecto"] <= 2.6, f"{n} productos dan aspecto {s['aspecto']:.2f}"


@pytest.mark.parametrize("n,filas", [(1, 1), (6, 1), (14, 2), (16, 3), (18, 3), (20, 3)])
def test_los_sets_chicos_conservan_su_reparto(n, filas):
    """Estos repartos son los que tenía la escalera fija original: cambiarlos
    altera composiciones que el usuario ya conoce."""
    assert _salida(n)["filas"] == filas


def test_el_producto_encoge_al_crecer_la_cantidad():
    """Es el comportamiento pedido: más productos → cada uno más chico, en vez
    de un archivo que crece sin límite."""
    altos = [_salida(n)["alto_prod"] for n in (14, 30, 60, 100)]
    assert altos == sorted(altos, reverse=True), f"no decrece: {altos}"


def test_mas_filas_que_antes_deja_el_producto_mas_grande():
    """A 60 productos, el reparto automático debe superar claramente a las 3
    filas fijas de antes — es la ganancia concreta del cambio."""
    antes = _salida(60, rows_count=3)["alto_prod"]
    ahora = _salida(60)["alto_prod"]
    assert ahora > antes * 1.4, f"antes {antes:.0f}px, ahora {ahora:.0f}px"


def test_el_aspecto_pedido_se_respeta():
    """aspect_w/aspect_h eran parámetros muertos: se asignaban y nunca se
    leían. Ahora deciden el reparto."""
    ancho = _salida(60, aspect_w=5, aspect_h=1)
    cuadrado = _salida(60, aspect_w=1, aspect_h=1)
    assert ancho["filas"] < cuadrado["filas"]
    assert ancho["aspecto"] > cuadrado["aspecto"]
