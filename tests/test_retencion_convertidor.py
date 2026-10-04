"""Las plantillas convertidas no se borran solas.

`sessions/_convertidor` no es una sesion: es la carpeta que CONTIENE las
conversiones. La retencion recorre `sessions/` entera y, sin un caso propio, la
trataba como una sesion anonima —busca un `bodegon.json` que ahi no existe, no
le encuentra dueño— y la borraba ENTERA a las 24 h, con todas las plantillas de
todo el mundo dentro. No es hipotetico: paso, y se perdieron las conversiones de
una tarde.

Una pieza convertida es trabajo del usuario, igual que una sesion de bodegon, y
se rige por la misma regla: cuota -> presupuesto de disco -> TTL largo, y la mas
nueva no se borra nunca.
"""
import json
import time

import pytest

import main


@pytest.fixture
def sesiones(tmp_path, monkeypatch):
    """Un SESSIONS_DIR de juguete, con su carpeta de conversiones."""
    monkeypatch.setattr(main, "SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(main, "CONVERTIDOR_DIR", tmp_path / "_convertidor")
    (tmp_path / "_convertidor").mkdir()
    return tmp_path


def _pieza(raiz, nombre, owner, edad_dias=0, peso=0):
    d = raiz / "_convertidor" / nombre
    d.mkdir(parents=True)
    creada = time.time() - edad_dias * 86400
    (d / "meta.json").write_text(json.dumps({
        "owner": owner, "created": creada, "filename": nombre + ".psd",
    }), encoding="utf-8")
    if peso:
        (d / "origen.psd").write_bytes(b"\0" * peso)
    import os
    os.utime(d, (creada, creada))
    return d


def test_la_carpeta_de_conversiones_sobrevive(sesiones):
    """Lo que fallaba: se borraba entera por no tener bodegon.json."""
    import os
    carpeta = sesiones / "_convertidor"
    viejo = time.time() - 10 * 86400
    os.utime(carpeta, (viejo, viejo))

    main._cleanup_old_sessions()

    assert carpeta.exists(), "la retencion se llevo la carpeta de conversiones"


def test_una_pieza_con_dueno_no_caduca_por_edad(sesiones):
    """Es trabajo del usuario: no se borra por vieja, como una sesion con dueño."""
    d = _pieza(sesiones, "p1", "ana", edad_dias=30)
    main._cleanup_old_sessions()
    assert d.exists()


def test_una_pieza_sin_dueno_si_caduca(sesiones):
    """Nadie a quien mostrarsela: TTL corto, como una sesion anonima."""
    d = _pieza(sesiones, "anon", None, edad_dias=5)
    main._cleanup_old_sessions()
    assert not d.exists()


def test_una_pieza_sin_dueno_recien_hecha_se_respeta(sesiones):
    d = _pieza(sesiones, "anon2", None, edad_dias=0)
    main._cleanup_old_sessions()
    assert d.exists()


def test_la_cuota_por_usuario_se_aplica_y_deja_las_mas_nuevas(sesiones, monkeypatch):
    monkeypatch.setattr(main, "SESSION_MAX_PER_USER", 3)
    piezas = [_pieza(sesiones, f"p{i}", "ana", edad_dias=i) for i in range(6)]
    main._cleanup_old_sessions()
    vivas = [p.name for p in piezas if p.exists()]
    assert vivas == ["p0", "p1", "p2"], "deben quedar las tres mas nuevas"


def test_la_mas_nueva_nunca_se_borra_aunque_pase_del_presupuesto(sesiones, monkeypatch):
    """Borrar la que se acaba de convertir seria borrar el trabajo en curso."""
    monkeypatch.setattr(main, "SESSION_MAX_MB_PER_USER", 0)   # presupuesto imposible
    nueva = _pieza(sesiones, "nueva", "ana", edad_dias=0, peso=4096)
    vieja = _pieza(sesiones, "vieja", "ana", edad_dias=1, peso=4096)
    main._cleanup_old_sessions()
    assert nueva.exists(), "la mas nueva se conserva siempre"
    assert not vieja.exists()


def test_las_piezas_de_un_usuario_no_afectan_a_las_de_otro(sesiones, monkeypatch):
    monkeypatch.setattr(main, "SESSION_MAX_PER_USER", 1)
    ana = _pieza(sesiones, "ana1", "ana", edad_dias=0)
    ana_vieja = _pieza(sesiones, "ana2", "ana", edad_dias=3)
    beto = _pieza(sesiones, "beto1", "beto", edad_dias=5)
    main._cleanup_old_sessions()
    assert ana.exists() and beto.exists()
    assert not ana_vieja.exists()


def test_una_pieza_con_meta_corrupta_no_rompe_la_limpieza(sesiones):
    d = sesiones / "_convertidor" / "rota"
    d.mkdir(parents=True)
    (d / "meta.json").write_text("{esto no es json", encoding="utf-8")
    main._cleanup_old_sessions()      # no debe levantar


def test_un_archivo_suelto_en_la_carpeta_no_rompe_nada(sesiones):
    (sesiones / "_convertidor" / "basura.txt").write_text("x", encoding="utf-8")
    main._cleanup_old_sessions()
