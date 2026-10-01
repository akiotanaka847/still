"""Mandar una plantilla convertida al Visor.

El Visor es donde la pieza se edita de verdad, y vive aparte: otro servidor y,
en produccion, otro dominio. Lo natural seria que pidiera el paquete con la
cookie de sesion de Still, pero esa cookie es SameSite=lax a proposito —entre
dominios distintos el navegador no la manda, y ponerla en SameSite=None seria
bajarle la guardia anti-CSRF a TODO Still por una sola funcion—.

Asi que no viaja la sesion: viaja un permiso firmado, corto y atado a UNA
pieza. Estos tests cuidan las dos mitades de ese trato: que el permiso valga
exactamente para lo que dice (y ni un minuto mas), y que no haga falta aflojar
nada del resto de Still para que funcione.
"""
import hashlib
import hmac
import importlib
import pathlib
import time
import urllib.parse

import pytest
from fastapi.testclient import TestClient

import main

PIEZA = "0281ae81-c8bc-4b73-bf88-a463f0d3e99f"


@pytest.fixture
def con_visor(monkeypatch):
    """Recarga main con un Visor configurado, y lo deja como estaba al salir."""
    monkeypatch.setenv("VISOR_URL", "https://visor.ejemplo.com/")
    importlib.reload(main)
    yield main
    monkeypatch.delenv("VISOR_URL", raising=False)
    importlib.reload(main)


class _Peticion:
    """Lo unico que _enlace_visor necesita de una Request."""

    def url_for(self, nombre, **kw):
        assert nombre == "convertidor_traspaso"
        return f"http://still.ejemplo.com/api/traspaso/{kw['token']}"


# ─── el enlace ────────────────────────────────────────────────────────────────

def test_sin_visor_configurado_no_hay_enlace():
    assert main.VISOR_URL == ""
    assert main._enlace_visor(PIEZA, _Peticion()) is None


def test_el_enlace_lleva_al_visor_con_el_paquete_a_cuestas(con_visor):
    enlace = con_visor._enlace_visor(PIEZA, _Peticion())
    partes = urllib.parse.urlsplit(enlace)
    assert f"{partes.scheme}://{partes.netloc}" == "https://visor.ejemplo.com"

    destino = urllib.parse.parse_qs(partes.query)["plantilla"][0]
    assert destino.startswith("http://still.ejemplo.com/api/traspaso/")
    assert con_visor._leer_traspaso(destino.rsplit("/", 1)[1]) == PIEZA


def test_la_barra_final_del_visor_no_se_duplica(con_visor):
    assert con_visor.VISOR_URL == "https://visor.ejemplo.com"
    assert "com//" not in con_visor._enlace_visor(PIEZA, _Peticion())


# ─── el permiso ───────────────────────────────────────────────────────────────

def test_el_permiso_abre_su_pieza():
    assert main._leer_traspaso(main._firmar_traspaso(PIEZA)) == PIEZA


def test_un_permiso_no_abre_otra_pieza():
    """Lo mas valioso que protege la firma: que no sea un comodin."""
    otra = "11111111-2222-3333-4444-555555555555"
    _, exp, firma = main._firmar_traspaso(PIEZA).split(".")
    with pytest.raises(main.HTTPException) as e:
        main._leer_traspaso(f"{otra}.{exp}.{firma}")
    assert e.value.status_code == 403


def test_una_firma_retocada_no_vale():
    token = main._firmar_traspaso(PIEZA)
    with pytest.raises(main.HTTPException) as e:
        main._leer_traspaso(token[:-1] + ("0" if token[-1] != "0" else "1"))
    assert e.value.status_code == 403


def test_alargar_la_vigencia_a_mano_no_vale():
    """La caducidad va firmada: reescribirla invalida el permiso entero."""
    pieza, exp, firma = main._firmar_traspaso(PIEZA).split(".")
    with pytest.raises(main.HTTPException) as e:
        main._leer_traspaso(f"{pieza}.{int(exp) + 86400}.{firma}")
    assert e.value.status_code == 403


def test_un_permiso_caducado_lo_dice_y_no_abre():
    exp = int(time.time()) - 1
    firma = hmac.new(main._get_secret().encode(), f"{PIEZA}|{exp}".encode(),
                     hashlib.sha256).hexdigest()[:32]
    with pytest.raises(main.HTTPException) as e:
        main._leer_traspaso(f"{PIEZA}.{exp}.{firma}")
    assert e.value.status_code == 410       # caducado, no "prohibido"


@pytest.mark.parametrize("basura", ["", "nada", "a.b", "a.b.c.d", "../../etc/passwd"])
def test_un_permiso_con_cualquier_forma_rara_se_rechaza(basura):
    with pytest.raises(main.HTTPException) as e:
        main._leer_traspaso(basura)
    assert e.value.status_code == 403


def test_la_vigencia_es_de_minutos_no_de_horas():
    assert 0 < main.TRASPASO_TTL_SEG <= 3600


# ─── lo que NO se afloja para que esto funcione ───────────────────────────────

def test_la_cookie_de_sesion_sigue_siendo_samesite_lax():
    """La razon de ser del permiso firmado: no tocar esto."""
    fuente = (pathlib.Path(__file__).resolve().parents[1] / "main.py").read_text(
        encoding="utf-8")
    assert 'same_site="lax"' in fuente
    assert 'same_site="none"' not in fuente.lower()


def test_el_traspaso_vive_fuera_del_guard_de_sesion():
    """Cuelga de /api/traspaso justamente porque /api/convertidor exige sesion."""
    assert not "/api/traspaso".startswith(main.PROTECTED_PREFIXES)
    assert "/api/convertidor".startswith(main.PROTECTED_PREFIXES)


def test_el_paquete_por_la_puerta_normal_sigue_pidiendo_sesion():
    with TestClient(main.app) as c:
        r = c.get(f"/api/convertidor/descargar/{PIEZA}/paquete")
    assert r.status_code == 401


def test_el_traspaso_no_sirve_para_pasearse_por_el_disco():
    with TestClient(main.app) as c:
        r = c.get("/api/traspaso/..%2F..%2Fmain.py.1.deadbeef")
    assert r.status_code in (403, 404)


def test_configurar_el_visor_abre_su_origen_en_cors(con_visor):
    """Sin esto el navegador bloquea la descarga sin error visible en el servidor."""
    assert "https://visor.ejemplo.com" in con_visor.ALLOWED_ORIGINS


def test_el_visor_no_afloja_cors_para_nadie_mas(con_visor):
    assert "*" not in con_visor.ALLOWED_ORIGINS
    # Solo el origen, nunca una ruta.
    assert all("/" not in o.split("//", 1)[1] for o in con_visor.ALLOWED_ORIGINS)


def test_sin_visor_cors_sigue_siendo_el_de_siempre():
    assert main.ALLOWED_ORIGINS == [
        "http://localhost:8000", "http://127.0.0.1:8000"]


# ─── la interfaz ──────────────────────────────────────────────────────────────

def test_el_boton_esta_cableado_en_la_interfaz():
    """Existe, nace oculto y solo lo enciende la respuesta del servidor."""
    html = (pathlib.Path(__file__).resolve().parents[1]
            / "static" / "index.html").read_text(encoding="utf-8")
    assert 'id="cv-visor"' in html
    marca = html.split('id="cv-visor"', 1)[1].split(">", 1)[0]
    assert "hidden" in marca
    assert "visor.hidden = !d.visor" in html
