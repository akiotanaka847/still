"""Claves de API: acceso de maquinas a Still.

Still nacio con un solo modo de entrar: cookie de sesion. Eso vale para una
persona con navegador y no vale para un automatismo (n8n, un script), que
tendria que guardar la contrasena de alguien y actuar en su nombre.

Estos tests fijan las propiedades que hacen que una clave sea segura de
repartir, y sobre todo la que es facil de perder en un refactor: que una clave
NO pueda crear mas claves.
"""
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture()
def entorno(tmp_path, monkeypatch):
    """Cada test con su propio fichero de claves y su propio usuario."""
    monkeypatch.setattr(main, "API_KEYS_FILE", tmp_path / "api_keys.json")
    monkeypatch.setattr(main, "USERS_FILE", tmp_path / "users.json")
    usuarios = {"ana": {"password": main._hash_pw("clave-de-ana-12345")}}
    (tmp_path / "users.json").write_text(json.dumps(usuarios), encoding="utf-8")

    # El login permite 8 intentos por usuario cada 5 minutos, y aqui hay mas de
    # ocho tests que entran como "ana". Sin vaciar el contador, los ultimos
    # fallarian con 429 por una razon que no tiene nada que ver con lo que
    # prueban.
    main._rl_store.clear()

    c = TestClient(main.app)
    r = c.post("/api/auth/login", json={"username": "ana", "password": "clave-de-ana-12345"})
    assert r.status_code == 200, r.text
    return c


def _crear(c, label="n8n"):
    r = c.post("/api/auth/api-keys", json={"label": label})
    assert r.status_code == 200, r.text
    return r.json()


def test_la_clave_solo_se_muestra_una_vez(entorno):
    d = _crear(entorno)
    assert d["key"].startswith(main.API_KEY_PREFIX + "_")
    # La lista no la trae, porque no se guarda.
    for k in entorno.get("/api/auth/api-keys").json()["keys"]:
        assert "key" not in k


def test_el_secreto_no_se_guarda_en_claro(entorno):
    """Lo que hay en disco tiene que ser inservible si alguien lo lee."""
    d = _crear(entorno)
    crudo = main.API_KEYS_FILE.read_text()
    secreto = d["key"].split("_")[2]
    assert secreto not in crudo, "el secreto esta guardado en claro"
    assert d["key"] not in crudo
    # Y lo que se guarda es el hash de ese secreto.
    reg = json.loads(crudo)[d["id"]]
    assert reg["hash"] == hashlib.sha256(secreto.encode()).hexdigest()


def test_una_maquina_entra_con_la_clave(entorno):
    d = _crear(entorno)
    c = TestClient(main.app)          # cliente limpio: sin cookies
    assert c.get("/api/bodegon/mine").status_code == 401
    r = c.get("/api/bodegon/mine", headers={"Authorization": f"Bearer {d['key']}"})
    assert r.status_code == 200, r.text


def test_una_clave_no_puede_crear_mas_claves(entorno):
    """LA PROPIEDAD QUE NO SE PUEDE PERDER.

    Si una clave pudiera crear otras, revocarla no serviria de nada: quien la
    robo se habria hecho mas antes de que nadie se diera cuenta. Por eso los
    endpoints de gestion preguntan por request.session y NO por _usuario().
    """
    d = _crear(entorno)
    c = TestClient(main.app)
    h = {"Authorization": f"Bearer {d['key']}"}
    assert c.post("/api/auth/api-keys", json={"label": "otra"}, headers=h).status_code == 401
    assert c.get("/api/auth/api-keys", headers=h).status_code == 401
    assert c.delete(f"/api/auth/api-keys/{d['id']}", headers=h).status_code == 401


def test_revocar_corta_el_acceso(entorno):
    d = _crear(entorno)
    c = TestClient(main.app)
    h = {"Authorization": f"Bearer {d['key']}"}
    assert c.get("/api/bodegon/mine", headers=h).status_code == 200
    assert entorno.delete(f"/api/auth/api-keys/{d['id']}").status_code == 200
    assert c.get("/api/bodegon/mine", headers=h).status_code == 401


def test_el_registro_sobrevive_a_la_revocacion(entorno):
    """No se borra, se marca: si algo raro paso, hay que poder mirar cuando se
    creo y cuando se uso por ultima vez."""
    d = _crear(entorno)
    entorno.delete(f"/api/auth/api-keys/{d['id']}")
    reg = json.loads(main.API_KEYS_FILE.read_text())[d["id"]]
    assert reg["revocada"] is True
    assert reg.get("revocada_en")


@pytest.mark.parametrize("mala", [
    "still_deadbeef_" + "0" * 32,      # inventada
    "still_xxx",                        # mal formada
    "",                                 # vacia
    "otracosa_deadbeef_" + "0" * 32,    # otro prefijo
    "still__" + "0" * 32,               # sin id
])
def test_las_claves_invalidas_no_entran(entorno, mala):
    c = TestClient(main.app)
    r = c.get("/api/bodegon/mine", headers={"Authorization": f"Bearer {mala}"})
    assert r.status_code == 401, f"{mala!r} entro"


def test_no_se_confirma_la_existencia_de_claves_ajenas(entorno, tmp_path):
    """Revocar una clave de otro da 404, igual que una que no existe. Si diera
    403 se estaria confirmando que existe."""
    # El fichero puede no existir todavia: este test no crea ninguna clave
    # propia antes.
    claves = json.loads(main.API_KEYS_FILE.read_text()) if main.API_KEYS_FILE.exists() else {}
    claves["aaaaaaaa"] = {"owner": "otro", "label": "x", "hash": "z",
                          "created": 0, "ultimo_uso": 0, "revocada": False}
    main.API_KEYS_FILE.write_text(json.dumps(claves))
    assert entorno.delete("/api/auth/api-keys/aaaaaaaa").status_code == 404
    assert entorno.delete("/api/auth/api-keys/bbbbbbbb").status_code == 404


def test_hay_un_tope_de_claves_por_usuario(entorno, monkeypatch):
    monkeypatch.setattr(main, "MAX_API_KEYS_POR_USUARIO", 2)
    _crear(entorno, "una")
    _crear(entorno, "dos")
    r = entorno.post("/api/auth/api-keys", json={"label": "tres"})
    assert r.status_code == 400
    assert "Revoke one" in r.text


def test_la_clave_necesita_nombre(entorno):
    """Una clave sin nombre es una clave que nadie sabe para que era, y que por
    eso nadie se atreve a revocar."""
    assert entorno.post("/api/auth/api-keys", json={"label": "   "}).status_code == 400
    assert entorno.post("/api/auth/api-keys", json={}).status_code == 400


def test_cada_clave_tiene_su_propio_contador_de_peticiones(entorno):
    """Si n8n se desboca, el limite frena a n8n — no a la persona que lo creo."""
    from unittest.mock import Mock

    req = Mock()
    req.state.api_key = "still_abcd1234_" + "0" * 32
    req.session = {"user": "ana"}
    assert main._rate_identity(req) == "key:abcd1234"

    req2 = Mock()
    req2.state = Mock(spec=[])        # sin api_key
    req2.session = {"user": "ana"}
    assert main._rate_identity(req2) == "ana"
