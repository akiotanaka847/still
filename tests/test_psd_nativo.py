"""El PSD que devolvemos es el del cliente, no una reconstruccion.

Antes `a_psd` construia un archivo nuevo desde cero: salia plano, en RGB y con
una capa de pixeles por elemento. De un PSD con 12 grupos, 10 objetos
inteligentes y 11 capas de texto quedaban 30 capas de pixeles y nada mas. Una
imprenta no podia tocar ni una palabra.

Ahora se abre el archivo original y solo se le cambian los nombres (ver
`psd-nativo/`, que envuelve al crate photocraft-psd). Todo lo demas vuelve a
salir tal cual. Estos tests cuidan las dos mitades del trato: que los nombres
cambien de verdad, y que no se pierda nada por el camino.

El binario puede no estar compilado —en una maquina recien clonada, o si
fallara su construccion— y entonces se usa el camino de antes. Peor resultado,
pero resultado: eso tambien se comprueba aqui.
"""
import pathlib
import shutil

import pytest
from psd_tools import PSDImage

import convertidor

PSD = (pathlib.Path(__file__).resolve().parents[2]
       / "PSDs Ejemplos" / "Cabecera 5 Central B.psd")

necesita_psd = pytest.mark.skipif(not PSD.exists(), reason=f"no esta {PSD.name}")
necesita_binario = pytest.mark.skipif(
    not convertidor._PSD_NATIVO.exists(),
    reason="psd-nativo sin compilar (cargo build --release en psd-nativo/)")


def _composicion(ruta):
    """Cuantas capas de cada tipo tiene un PSD."""
    p = PSDImage.open(ruta)
    conteo = {}
    for c in p.descendants():
        conteo[c.kind] = conteo.get(c.kind, 0) + 1
    return p.color_mode.name, (p.width, p.height), conteo


@pytest.fixture(scope="module")
def estructura():
    return convertidor.proponer_roles(convertidor.analizar(PSD), api_key=None)


# ─── la llave que hace posible todo esto ──────────────────────────────────────

@necesita_psd
def test_cada_capa_lleva_su_identificador_de_photoshop(estructura):
    """Sin lyid no hay forma de volver a encontrar una capa en el archivo.

    El nombre no sirve —una plantilla tiene varios "Rectángulo 1"— y la posicion
    tampoco, porque cambia en cuanto se toca el documento.
    """
    capas = estructura["capas"]
    ids = [c.get("lyid") for c in capas]
    assert all(i is not None for i in ids), "alguna capa se quedo sin lyid"
    assert len(set(ids)) == len(ids), "hay lyid repetidos: no servirian de llave"


def test_un_psd_sin_identificadores_no_rompe_el_analisis():
    """Un archivo viejo puede no traerlos; entonces esa capa no se renombra."""
    class _Capa:
        tagged_blocks = {}

        def __getattr__(self, _):
            raise AttributeError
    assert convertidor._lyid(_Capa()) is None


# ─── el PSD que sale ──────────────────────────────────────────────────────────

@necesita_psd
@necesita_binario
def test_el_psd_que_sale_es_el_del_cliente(estructura, tmp_path):
    salida = tmp_path / "pieza.psd"
    convertidor.a_psd(PSD, estructura, salida)

    modo_o, tam_o, comp_o = _composicion(PSD)
    modo_n, tam_n, comp_n = _composicion(salida)

    assert modo_n == modo_o == "CMYK", "el modo de color tiene que sobrevivir"
    assert tam_n == tam_o
    assert comp_n == comp_o, f"la composicion cambio: {comp_o} -> {comp_n}"


@necesita_psd
@necesita_binario
def test_las_capas_de_texto_siguen_siendo_texto(estructura, tmp_path):
    """Lo que de verdad pedia el cliente: poder cambiar una palabra."""
    salida = tmp_path / "pieza.psd"
    convertidor.a_psd(PSD, estructura, salida)

    textos = [c for c in PSDImage.open(salida).descendants() if c.kind == "type"]
    assert len(textos) == 11
    assert any(t.text and t.text.strip() for t in textos), "el contenido se perdio"


@necesita_psd
@necesita_binario
def test_los_objetos_inteligentes_siguen_siendolo(estructura, tmp_path):
    salida = tmp_path / "pieza.psd"
    convertidor.a_psd(PSD, estructura, salida)
    objetos = [c for c in PSDImage.open(salida).descendants() if c.kind == "smartobject"]
    assert len(objetos) == 10


@necesita_psd
@necesita_binario
def test_los_nombres_son_los_que_decidio_el_convertidor(estructura, tmp_path):
    salida = tmp_path / "pieza.psd"
    convertidor.a_psd(PSD, estructura, salida)

    nombres = {c.name for c in PSDImage.open(salida).descendants()}
    esperados = {c.get("nombre_limpio") or convertidor._limpiar_nombre(c["nombre"])
                 for c in estructura["capas"]}
    comunes = nombres & esperados
    assert len(comunes) > 25, f"solo {len(comunes)} nombres aplicados"
    # Y ya no quedan los originales con mayusculas y espacios.
    assert "Objeto inteligente vectorial" not in nombres


# ─── el camino de respaldo ────────────────────────────────────────────────────

@necesita_psd
def test_sin_el_binario_se_sigue_entregando_un_psd(estructura, tmp_path, monkeypatch):
    """Peor resultado, pero resultado: nadie se queda sin descarga."""
    monkeypatch.setattr(convertidor, "_PSD_NATIVO", pathlib.Path("/no/existe"))
    salida = tmp_path / "pieza.psd"
    convertidor.a_psd(PSD, estructura, salida)
    assert salida.exists()
    modo, tam, comp = _composicion(salida)
    assert tam == (6850, 1004)
    assert comp, "el camino de respaldo tiene que dar algo"


def test_el_respaldo_se_usa_cuando_el_binario_falla(tmp_path, monkeypatch):
    """Un binario que devuelve error no debe dejar un PSD a medias."""
    falso = tmp_path / "falso"
    falso.write_text("#!/bin/sh\nexit 1\n")
    falso.chmod(0o755)
    monkeypatch.setattr(convertidor, "_PSD_NATIVO", falso)
    ok = convertidor._renombrar_en_sitio(
        pathlib.Path("da-igual.psd"),
        {"capas": [{"lyid": 1, "nombre": "x", "nombre_limpio": "x"}]},
        tmp_path / "salida.psd")
    assert ok is False


def test_una_estructura_sin_lyid_no_intenta_el_camino_nativo(tmp_path):
    ok = convertidor._renombrar_en_sitio(
        pathlib.Path("da-igual.psd"),
        {"capas": [{"nombre": "x", "nombre_limpio": "x"}]},
        tmp_path / "salida.psd")
    assert ok is False


# ─── el crate copiado ─────────────────────────────────────────────────────────

def test_queda_dicho_de_donde_viene_el_crate():
    """Codigo de terceros copiado al repo: tiene que decir su origen y licencia."""
    raiz = pathlib.Path(__file__).resolve().parents[1]
    proc = raiz / "psd-nativo" / "vendor" / "PROCEDENCIA.md"
    assert proc.exists(), "falta la nota de procedencia del crate copiado"
    texto = proc.read_text(encoding="utf-8")
    assert "storytold/photocraft" in texto
    assert "47f9306" in texto, "hay que fijar el commit del que se copio"
    assert "MIT OR Apache-2.0" in texto
