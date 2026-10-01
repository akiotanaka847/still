"""La resolucion del PSD viaja en el paquete.

Los pixeles de un lienzo no dicen a que tamaño se imprime: 6850 px pueden ser
58 cm o 116 cm segun la resolucion del documento. Quien reciba el paquete y
genere un PDF tiene que saberlo o tiene que adivinar, y adivinar mal significa
mandar a imprenta una pieza al doble o la mitad de su tamaño.

Photoshop guarda ese dato en punto fijo de 16 bits (el valor crudo viene
multiplicado por 65536). Olvidar esa division es el error silencioso que estos
tests evitan: daria 19.660.800 dpi y un tamaño fisico de cero.
"""
import pathlib

import pytest

import convertidor

PSD = (pathlib.Path(__file__).resolve().parents[1]
       / "sessions" / "_convertidor" / "0281ae81-c8bc-4b73-bf88-a463f0d3e99f"
       / "origen.psd")

pytestmark = pytest.mark.skipif(
    not PSD.exists(), reason="hace falta un PSD convertido de ejemplo")


class _SinResolucion:
    class _record:
        class image_resources:
            @staticmethod
            def get_data(_):
                raise KeyError("no hay ResolutionInfo")


class _Absurda:
    """Un PSD cuyo dato de resolucion no se puede creer."""

    def __init__(self, crudo):
        bruto = crudo

        class _ri:
            horizontal = bruto

        class _record:
            class image_resources:
                @staticmethod
                def get_data(_):
                    return _ri

        self._record = _record


def test_lee_la_resolucion_deshaciendo_el_punto_fijo():
    from psd_tools import PSDImage
    assert convertidor._dpi(PSDImage.open(PSD)) == 300.0


def test_un_psd_sin_resolucion_no_inventa_una():
    """Mejor decir "no se" que mentir: quien exporte decide que asumir."""
    assert convertidor._dpi(_SinResolucion) is None


@pytest.mark.parametrize("crudo", [0, 1, 19660800 * 100, -65536])
def test_una_resolucion_absurda_se_descarta(crudo):
    assert convertidor._dpi(_Absurda(crudo)) is None


def test_una_resolucion_creible_se_acepta():
    assert convertidor._dpi(_Absurda(150 * 65536)) == 150.0
    assert convertidor._dpi(_Absurda(72 * 65536)) == 72.0


def test_el_analisis_la_lleva_en_el_lienzo():
    assert convertidor.analizar(PSD)["lienzo"]["dpi"] == 300.0


def test_el_paquete_la_entrega(tmp_path):
    estructura = {
        "archivo": "x.psd",
        "lienzo": {"ancho": 6850, "alto": 1004, "modo": "CMYK", "dpi": 300.0},
        "capas": [], "escala_render": 1.0,
    }
    assert convertidor.paquete(estructura, {})["lienzo"]["dpi"] == 300.0


def test_el_paquete_no_se_rompe_si_el_psd_no_la_traia():
    """El campo existe siempre; vacio significa "se desconoce"."""
    estructura = {
        "archivo": "x.psd",
        "lienzo": {"ancho": 100, "alto": 100, "modo": "RGB"},
        "capas": [], "escala_render": 1.0,
    }
    paq = convertidor.paquete(estructura, {})
    assert "dpi" in paq["lienzo"] and paq["lienzo"]["dpi"] is None


def test_el_tamano_fisico_que_resulta_es_el_de_una_cenefa_real():
    """Control de cordura: 6850 px a 300 dpi son 58 cm, no 58 metros."""
    lienzo = convertidor.analizar(PSD)["lienzo"]
    ancho_cm = lienzo["ancho"] / lienzo["dpi"] * 2.54
    alto_cm = lienzo["alto"] / lienzo["dpi"] * 2.54
    assert 50 < ancho_cm < 70
    assert 5 < alto_cm < 12
