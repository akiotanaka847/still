"""Reparto de filas: mejor forma de pieza SIN perder resolución.

El requisito que manda acá es explícito del usuario: **no se puede perder la
resolución que produce producción**. Todo lo demás se subordina a eso.

Historia, porque explica la forma de estas pruebas:

  1. Las filas estaban fijas (1 hasta 6, 2 hasta 15, 3 en adelante) y
     aspect_w/aspect_h se asignaban pero NUNCA se leían — parámetros muertos.
     Con el canvas exacto al contenido, 3 filas de N crecen solo a lo ancho: 60
     productos daban 12510x2975, una tira de 4.2:1.
  2. Al arreglarlo se convirtió MAX_CANVAS_DIM en tope real en los dos
     sentidos, porque su comentario decía "tope duro del lado mayor del canvas
     final". Medido contra producción, eso BAJABA el alto de cada producto de
     832 a 585 px en un bodegón de 60. Se revirtió: _compute_export_scale
     nunca reduce.
  3. La primera versión de estas pruebas usaba productos sintéticos todos de
     razón 0.5 y por eso no detectó que el reparto nuevo empeoraba los sets de
     16, 20 y 25. El catálogo real va de 0.23 a 2.80. Acá se prueban mezclas.
"""
import pytest

from layout import compute_layout_any
from renderer import _compute_export_scale

# Proporciones tomadas del catálogo real: botella alta, lata, caja casi
# cuadrada, bolsa ancha, six-pack.
MEZCLAS = {
    "altos": [0.30, 0.42, 0.35, 0.50],
    "variados": [0.42, 0.66, 1.05, 0.31, 2.10, 0.55],
    "anchos": [1.40, 2.80, 1.10, 0.90],
}
CANTIDADES = [1, 2, 6, 10, 14, 15, 16, 18, 20, 22, 25, 30, 40, 50, 60]


def _productos(n, mezcla):
    razones = MEZCLAS[mezcla]
    return [{"name": f"sku{i}", "width": int(1000 * razones[i % len(razones)]),
             "height": 1000, "filepath": f"/dev/null/{i}.png"} for i in range(n)]


def _filas_de_produccion(n):
    """La escalera fija que corre hoy en producción."""
    return 1 if n <= 6 else (2 if n <= 15 else 3)


def _salida(n, mezcla, rows_count=0, **kw):
    prods = _productos(n, mezcla)
    layout = compute_layout_any(prods, sort_strategy="auto", base_height=760,
                               rows_count=rows_count, **kw)
    esc = _compute_export_scale(layout)
    cw, ch = layout["canvas_width"] * esc, layout["canvas_height"] * esc
    return {"filas": layout["rows_count"], "w": cw, "h": ch, "aspecto": cw / ch,
            "alto_prod": sum(it["sh"] for it in layout["items"]) / n * esc}


# ─── EL REQUISITO ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("mezcla", list(MEZCLAS))
@pytest.mark.parametrize("n", CANTIDADES)
def test_nunca_se_pierde_resolucion_frente_a_produccion(n, mezcla):
    """La prueba que manda. Si esta falla, el cambio no se despliega."""
    produccion = _salida(n, mezcla, rows_count=_filas_de_produccion(n))["alto_prod"]
    ahora = _salida(n, mezcla)["alto_prod"]
    assert ahora >= produccion * 0.995, (
        f"{n} productos ({mezcla}): producción da {produccion:.0f}px, "
        f"esto da {ahora:.0f}px")


def test_la_exportacion_nunca_reduce():
    """_compute_export_scale debe devolver siempre >= 1.0. Es la garantía
    estructural detrás de la prueba de arriba: sin ella, cualquier cambio en
    el reparto de filas puede volver a bajar la resolución sin avisar."""
    for mezcla in MEZCLAS:
        for n in CANTIDADES:
            prods = _productos(n, mezcla)
            layout = compute_layout_any(prods, sort_strategy="auto", base_height=760)
            assert _compute_export_scale(layout) >= 1.0, f"{n} ({mezcla})"


# ─── LA MEJORA ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("mezcla", list(MEZCLAS))
@pytest.mark.parametrize("n", [16, 20, 30, 40, 60])
def test_la_pieza_no_es_una_tira(n, mezcla):
    """Con 3 filas fijas, 60 productos daban 4.2:1 — inusable como pieza."""
    a = _salida(n, mezcla)["aspecto"]
    assert a <= 3.0, f"{n} productos ({mezcla}) dan aspecto {a:.2f}"


@pytest.mark.parametrize("mezcla", list(MEZCLAS))
def test_a_muchos_productos_la_forma_mejora_de_verdad(mezcla):
    """No basta con no empeorar: a 60 productos el reparto tiene que arreglar
    la tira, que es el problema que originó todo esto."""
    prod = _salida(60, mezcla, rows_count=3)["aspecto"]
    ahora = _salida(60, mezcla)["aspecto"]
    assert ahora < prod * 0.75, f"{mezcla}: producción {prod:.2f}, ahora {ahora:.2f}"


# ─── LO QUE NO DEBE CAMBIAR ──────────────────────────────────────────────────

@pytest.mark.parametrize("n,filas", [(1, 1), (6, 1), (10, 2), (14, 2), (15, 2)])
def test_con_productos_normales_los_sets_chicos_conservan_su_escalera(n, filas):
    """Una hilera en sets chicos es decisión estética, no un límite: optimizar
    por tamaño elegía 2 filas con 6 productos (84% más grandes, peor pieza).
    Se conserva cuando el reparto cabe, que es el caso normal del bodegón:
    envases más altos que anchos."""
    assert _salida(n, "altos")["filas"] == filas


@pytest.mark.parametrize("n", [6, 10, 14, 15])
def test_con_productos_anchos_el_set_chico_deja_de_estirarse(n):
    """La escalera fija se respeta solo si cabe. Con envases anchos una hilera
    de 6 daba una tira de 9.97:1 — el mismo defecto que a 60 productos, pero en
    sets chicos, y estaba igual de presente antes de este cambio."""
    fija = _salida(n, "anchos", rows_count=_filas_de_produccion(n))
    ahora = _salida(n, "anchos")
    assert ahora["aspecto"] < fija["aspecto"], (
        f"{n}: fija {fija['aspecto']:.2f}, ahora {ahora['aspecto']:.2f}")
    assert ahora["alto_prod"] >= fija["alto_prod"] * 0.995


def test_el_aspecto_pedido_se_respeta():
    """aspect_w/aspect_h eran parámetros muertos: se asignaban y nunca se
    leían, así que el control que el frontend ya enviaba no hacía nada."""
    ancho = _salida(60, "variados", aspect_w=5, aspect_h=1)
    cuadrado = _salida(60, "variados", aspect_w=1, aspect_h=1)
    assert ancho["filas"] < cuadrado["filas"]
    assert ancho["aspecto"] > cuadrado["aspecto"]
