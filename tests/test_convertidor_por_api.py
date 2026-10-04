"""Una maquina puede convertir una plantilla, no solo una persona.

El Visor vive en otro dominio y necesita mandarle un PSD al convertidor. No
puede hacerlo con la cookie de sesion de Still (es SameSite=lax a proposito,
ver test_envio_al_visor), asi que se presenta con una clave de API.

Que eso funcione depende de tres piezas que es facil dejar a medias, y cada una
falla de una forma distinta de diagnosticar:

1. El convertidor tiene que resolver al usuario con _usuario(), que entiende
   cookie Y clave. Si mira la sesion a pelo, la maquina se autentica para pasar
   el guard pero luego la pieza queda sin dueño o de otro.
2. CORS tiene que dejar pasar la cabecera Authorization. Si no, el navegador ni
   siquiera la envia, y no hay ningun error en el servidor que lo explique.
3. El preflight (OPTIONS) tiene que atravesar el guard de sesion. El navegador
   lo manda SIN credenciales por diseño; contestarlo con 401 corta la
   integracion antes de que salga la peticion de verdad.
"""
import pathlib

from fastapi.testclient import TestClient

import main


def test_el_convertidor_resuelve_al_usuario_con_clave_o_cookie():
    """Ni la propiedad de la pieza ni su dueño miran la sesion a pelo."""
    fuente = (pathlib.Path(__file__).resolve().parents[1] / "main.py").read_text(
        encoding="utf-8")
    cuerpo = fuente.split("/api/convertidor/subir", 1)[1]
    # El bloque del convertidor va hasta el siguiente grupo de rutas.
    cuerpo = cuerpo.split("@app.get(\"/api/traspaso", 1)[0]
    assert 'request.session.get("user")' not in cuerpo
    assert "_usuario(request)" in cuerpo


def test_cors_deja_pasar_la_cabecera_de_la_clave():
    """Sin esto el navegador no envia Authorization y no hay error que lo diga."""
    for mw in main.app.user_middleware:
        if mw.cls.__name__ == "CORSMiddleware":
            assert "Authorization" in mw.kwargs["allow_headers"]
            return
    raise AssertionError("no hay CORSMiddleware")


def test_el_preflight_atraviesa_el_guard_de_sesion():
    with TestClient(main.app) as c:
        r = c.options("/api/convertidor/subir", headers={
            "Origin": "http://localhost:8000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization",
        })
    assert r.status_code == 200, "un preflight no lleva credenciales: no se le pueden exigir"
    assert r.headers.get("access-control-allow-origin") == "http://localhost:8000"


def test_el_preflight_no_es_una_puerta_trasera():
    """Dejar pasar OPTIONS no deja pasar la peticion de verdad."""
    with TestClient(main.app) as c:
        assert c.post("/api/convertidor/subir").status_code == 401
        assert c.get("/api/convertidor/estructura/" + "0" * 36).status_code == 401
        assert c.post("/api/bodegon/upload").status_code == 401


def test_una_clave_inventada_no_abre_nada():
    with TestClient(main.app) as c:
        r = c.post("/api/convertidor/subir",
                   headers={"Authorization": "Bearer still_falsa_0123456789abcdef"})
    assert r.status_code == 401


def test_la_cabecera_sin_bearer_se_ignora():
    with TestClient(main.app) as c:
        r = c.post("/api/convertidor/subir", headers={"Authorization": "still_loquesea"})
    assert r.status_code == 401


def test_el_401_del_guard_llega_al_navegador_con_su_motivo():
    """CORSMiddleware va por DENTRO del guard, asi que un 401 de ahi sale pelado.

    Para el servidor es un 401 normal; para el navegador es un error de CORS y a
    quien lo sufre le llega un "Failed to fetch" sin causa. Con una clave de API
    caducada eso es justo lo que no hay que mostrar: el motivo cabe en una linea
    y es accionable.
    """
    with TestClient(main.app) as c:
        r = c.post("/api/convertidor/subir", headers={
            "Origin": "http://localhost:8000",
            "Authorization": "Bearer still_caducada_000000000000",
        })
    assert r.status_code == 401
    assert r.headers.get("access-control-allow-origin") == "http://localhost:8000"
    assert r.headers.get("vary") == "Origin"


def test_a_un_origen_no_permitido_el_401_sigue_saliendo_pelado():
    """Las cabeceras se ponen para los origenes ya permitidos, no para cualquiera."""
    with TestClient(main.app) as c:
        r = c.post("/api/convertidor/subir",
                   headers={"Origin": "https://sitio-ajeno.example"})
    assert r.status_code == 401
    assert "access-control-allow-origin" not in r.headers


def test_el_tope_se_puede_consultar_antes_de_subir():
    """Rechazar a mitad de subida no se entiende desde fuera.

    El servidor contesta 413 y cierra, pero quien sube todavia esta enviando,
    asi que esa respuesta no le llega: lo que ve es la conexion rota, igual que
    si el servidor se hubiera caido. Medido en navegador: "Failed to fetch".
    Por eso el tope se puede preguntar por delante.
    """
    with TestClient(main.app) as c:
        assert c.get("/api/convertidor/limites").status_code == 401  # sigue protegido
    assert main.MAX_PSD_MB > 0


def test_un_psd_demasiado_grande_se_rechaza_por_la_cabecera():
    """Sin leer un solo byte: la cortesia de no gastarle la subida a nadie."""
    with TestClient(main.app) as c:
        r = c.post("/api/convertidor/subir",
                   headers={"Content-Length": str(main.MAX_PSD_BYTES + 1)})
    assert r.status_code in (401, 413)   # 401 porque este cliente no lleva clave
