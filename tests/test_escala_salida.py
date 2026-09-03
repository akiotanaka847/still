"""La pieza debe degradar con gracia al crecer la cantidad de productos.

Blinda lo que se rompió de verdad y puede volver a romperse en silencio:

  1. La salida nunca pasa de MAX_CANVAS_DIM. El tope existía como constante
     pero solo aplicaba HACIA ARRIBA, así que 60 productos salían a 38 MP.
  2. La pieza no se vuelve una tira ni una columna. Con 3 filas fijas, 60
     productos daban 4.2:1.
  3. Ningún reparto automático es PEOR que el fijo de antes. Esta es la que
     importa y la que faltaba: una primera versión de _elegir_filas mejoraba
     el caso de 60 y empeoraba 16, 20 y 25 sin que nada avisara.

Sobre las proporciones: la primera versión de estas pruebas usaba productos
sintéticos todos de razón 0.5 y por eso NO detectó esas regresiones — el
catálogo real va de 0.23 a 2.80 y la razón media cambia con la mezcla, que es
justo lo que hacía cambiar la decisión. Acá se prueban varias mezclas.
"""
import pytest

from layout import compute_layout_any
from renderer import MAX_CANVAS_DIM, _compute_export_scale

# Mezclas de proporciones tomadas del catálogo real: botella alta, lata,
# caja casi cuadrada, bolsa ancha.
MEZCLAS = {
    "altos": [0.30, 0.42, 0.35, 0.50],
    "variados": [0.42, 0.66, 1.05, 0.31, 2.10, 0.55],
    "anchos": [1.40, 2.80, 1.10, 0.90],
}


def _productos(n, mezcla="variados"):
    razones = MEZCLAS[mezcla]
    h = 1000
    return [{"name": f"sku{i}", "width": int(h * razones[i % len(razones)]),
             "height": h, "filepath": f"/dev/null/{i}.png"} for i in range(n)]


def _salida(n, mezcla="variados", **kw):
    layout = compute_layout_any(_productos(n, mezcla), sort_strategy="auto",
                               base_height=760, **kw)
    esc = _compute_export_scale(layout)
    cw, ch = layout["canvas_width"] * esc, layout["canvas_height"] * esc
    return {"filas": layout["rows_count"], "w": cw, "h": ch, "aspecto": cw / ch,
            "alto_prod": sum(it["sh"] for it in layout["items"]) / n * esc}


def _filas_viejas(n):
    """La escalera fija que había antes: es la referencia a no empeorar."""
    return 1 if n <= 6 else (2 if n <= 15 else 3)


CANTIDADES = [1, 2, 6, 10, 14, 15, 16, 18, 20, 22, 25, 30, 40, 50, 60, 100]


@pytest.mark.parametrize("mezcla", list(MEZCLAS))
@pytest.mark.parametrize("n", CANTIDADES)
def test_la_salida_respeta_el_tope_de_lienzo(n, mezcla):
    s = _salida(n, mezcla)
    assert max(s["w"], s["h"]) <= MAX_CANVAS_DIM + 1, (
        f"{n} productos ({mezcla}) dan {s['w']:.0f}x{s['h']:.0f}")


@pytest.mark.parametrize("mezcla", list(MEZCLAS))
@pytest.mark.parametrize("n", [16, 20, 30, 40, 60, 100])
def test_la_pieza_no_es_una_tira_ni_una_columna(n, mezcla):
    a = _salida(n, mezcla)["aspecto"]
    assert 0.55 <= a <= 2.7, f"{n} productos ({mezcla}) dan aspecto {a:.2f}"


@pytest.mark.parametrize("mezcla", list(MEZCLAS))
@pytest.mark.parametrize("n", CANTIDADES)
def test_ningun_reparto_automatico_empeora_el_de_antes(n, mezcla):
    """La regresión que la versión sintética de esta prueba no vio."""
    antes = _salida(n, mezcla, rows_count=_filas_viejas(n))["alto_prod"]
    ahora = _salida(n, mezcla)["alto_prod"]
    assert ahora >= antes * 0.99, (
        f"{n} productos ({mezcla}): antes {antes:.0f}px, ahora {ahora:.0f}px")


@pytest.mark.parametrize("n,filas", [(1, 1), (6, 1), (10, 2), (14, 2), (15, 2)])
def test_con_productos_normales_los_sets_chicos_conservan_su_escalera(n, filas):
    """Una hilera en sets chicos es decisión estética, no un límite: optimizar
    por tamaño elegía 2 filas con 6 productos (84% más grandes, peor pieza).

    Se conserva cuando el reparto CABE bajo el tope, que es el caso normal del
    bodegón: envases más altos que anchos (botellas, latas, cartones)."""
    assert _salida(n, "altos")["filas"] == filas


@pytest.mark.parametrize("n", [6, 10, 14, 15])
def test_con_productos_anchos_el_set_chico_deja_de_ser_una_tira(n):
    """La escalera fija se respeta solo si cabe. Con envases anchos (bolsas,
    six-packs) una hilera daba una tira de 9.97:1 y el producto salía a 375 px
    cuando podía salir al doble — el mismo defecto que a 60 productos, pero en
    sets chicos, y estaba igual de presente antes de este cambio."""
    fija = _salida(n, "anchos", rows_count=_filas_viejas(n))
    ahora = _salida(n, "anchos")
    assert ahora["aspecto"] < 2.7, f"sigue siendo tira: {ahora['aspecto']:.2f}"
    assert ahora["alto_prod"] > fija["alto_prod"] * 1.2, (
        f"fija {fija['alto_prod']:.0f}px, ahora {ahora['alto_prod']:.0f}px")


def test_no_hay_escalon_entre_15_y_16_productos():
    """Con la escalera fija aplicada sin condición, pasar de 15 a 16 productos
    anchos duplicaba el tamaño del producto (313 -> 599 px) porque recién ahí
    entraba el cálculo. Un producto más no puede cambiar tanto la pieza."""
    for mezcla in MEZCLAS:
        a = _salida(15, mezcla)["alto_prod"]
        b = _salida(16, mezcla)["alto_prod"]
        assert abs(b - a) / a < 0.25, f"{mezcla}: 15 -> {a:.0f}px, 16 -> {b:.0f}px"


@pytest.mark.parametrize("mezcla", list(MEZCLAS))
def test_el_producto_encoge_al_crecer_la_cantidad(mezcla):
    altos = [_salida(n, mezcla)["alto_prod"] for n in (14, 30, 60, 100)]
    assert altos == sorted(altos, reverse=True), f"{mezcla}: no decrece {altos}"


@pytest.mark.parametrize("mezcla", list(MEZCLAS))
def test_a_60_productos_la_ganancia_es_grande(mezcla):
    antes = _salida(60, mezcla, rows_count=3)["alto_prod"]
    ahora = _salida(60, mezcla)["alto_prod"]
    assert ahora > antes * 1.3, f"{mezcla}: antes {antes:.0f}px, ahora {ahora:.0f}px"


def test_el_aspecto_pedido_se_respeta():
    """aspect_w/aspect_h eran parámetros muertos: se asignaban y nunca se leían."""
    ancho = _salida(60, aspect_w=5, aspect_h=1)
    cuadrado = _salida(60, aspect_w=1, aspect_h=1)
    assert ancho["filas"] < cuadrado["filas"]
    assert ancho["aspecto"] > cuadrado["aspecto"]
