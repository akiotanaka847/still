"""Retención de sesiones: que el disco deje de crecer sin techo.

El bug: `_cleanup_old_sessions` salteaba con `continue` toda sesión que
tuviera `owner`, así que las sesiones autenticadas —o sea todas las reales—
no caducaban nunca. Medido antes del arreglo: 1.4 GB en 47 sesiones, y la
limpieza además corría UNA sola vez, al arrancar.

Estas pruebas usan un directorio temporal, nunca las sesiones reales.
"""
import json
import time

import pytest

import main


def _sesion(dirbase, nombre, owner=None, creada=None, mb=1):
    d = dirbase / nombre
    (d / "products").mkdir(parents=True)
    info = {"session_id": nombre, "images": []}
    if owner:
        info["owner"] = owner
    if creada is not None:
        info["created"] = creada
    (d / "bodegon.json").write_text(json.dumps(info), encoding="utf-8")
    (d / "products" / "p.bin").write_bytes(b"\0" * int(mb * 1024 * 1024))
    if creada is not None:
        import os
        os.utime(d, (creada, creada))
    return d


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "SESSIONS_DIR", tmp_path)
    return tmp_path


def test_las_sesiones_con_dueno_ya_no_son_inmortales(dirs, monkeypatch):
    """El corazón del bug: antes esto dejaba las 80 en disco."""
    monkeypatch.setattr(main, "SESSION_MAX_PER_USER", 5)
    monkeypatch.setattr(main, "SESSION_MAX_MB_PER_USER", 10_000)
    ahora = time.time()
    for i in range(80):
        _sesion(dirs, f"s{i:03d}", owner="ana", creada=ahora - i * 60)
    main._cleanup_old_sessions()
    assert len(list(dirs.iterdir())) == 5


def test_se_conservan_las_mas_nuevas(dirs, monkeypatch):
    monkeypatch.setattr(main, "SESSION_MAX_PER_USER", 3)
    monkeypatch.setattr(main, "SESSION_MAX_MB_PER_USER", 10_000)
    ahora = time.time()
    for i in range(10):
        _sesion(dirs, f"s{i}", owner="ana", creada=ahora - i * 3600)
    main._cleanup_old_sessions()
    quedan = sorted(p.name for p in dirs.iterdir())
    assert quedan == ["s0", "s1", "s2"], quedan


def test_el_presupuesto_de_disco_manda_sobre_la_cuota(dirs, monkeypatch):
    """60 sesiones pueden ser 200 MB u 8 GB: la cuota de cantidad no protege
    el disco por sí sola."""
    monkeypatch.setattr(main, "SESSION_MAX_PER_USER", 50)
    monkeypatch.setattr(main, "SESSION_MAX_MB_PER_USER", 5)
    ahora = time.time()
    for i in range(20):
        _sesion(dirs, f"s{i:02d}", owner="ana", creada=ahora - i * 60, mb=2)
    main._cleanup_old_sessions()
    quedan = list(dirs.iterdir())
    usado = sum(main._dir_size(p) for p in quedan)
    # Tope real: 5 MB de presupuesto con sesiones de 2 MB deja 2 sesiones.
    assert usado <= 5 * 1024 * 1024, f"{usado/1e6:.1f} MB en {len(quedan)} sesiones"


def test_la_mas_nueva_se_conserva_aunque_no_entre_en_el_presupuesto(dirs, monkeypatch):
    """Es la sesión que el usuario tiene abierta: borrarla por cuota sería
    borrarle el trabajo en curso."""
    monkeypatch.setattr(main, "SESSION_MAX_PER_USER", 50)
    monkeypatch.setattr(main, "SESSION_MAX_MB_PER_USER", 1)   # menos que una sesión
    ahora = time.time()
    for i in range(4):
        _sesion(dirs, f"s{i}", owner="ana", creada=ahora - i * 60, mb=3)
    main._cleanup_old_sessions()
    quedan = sorted(p.name for p in dirs.iterdir())
    assert quedan == ["s0"], quedan


def test_la_cuota_es_por_usuario_no_global(dirs, monkeypatch):
    monkeypatch.setattr(main, "SESSION_MAX_PER_USER", 2)
    monkeypatch.setattr(main, "SESSION_MAX_MB_PER_USER", 10_000)
    ahora = time.time()
    for u in ("ana", "beto"):
        for i in range(5):
            _sesion(dirs, f"{u}{i}", owner=u, creada=ahora - i * 60)
    main._cleanup_old_sessions()
    quedan = [p.name for p in dirs.iterdir()]
    assert sum(1 for q in quedan if q.startswith("ana")) == 2
    assert sum(1 for q in quedan if q.startswith("beto")) == 2


def test_las_anonimas_siguen_caducando_por_edad(dirs, monkeypatch):
    """Comportamiento de siempre: no hay a quién mostrárselas."""
    monkeypatch.setattr(main, "SESSION_TTL_HOURS", 24)
    ahora = time.time()
    _sesion(dirs, "vieja", creada=ahora - 48 * 3600)
    _sesion(dirs, "nueva", creada=ahora - 1 * 3600)
    main._cleanup_old_sessions()
    quedan = sorted(p.name for p in dirs.iterdir())
    assert quedan == ["nueva"], quedan


def test_el_ttl_largo_limpia_cuentas_abandonadas(dirs, monkeypatch):
    monkeypatch.setattr(main, "SESSION_MAX_PER_USER", 60)
    monkeypatch.setattr(main, "SESSION_MAX_MB_PER_USER", 10_000)
    monkeypatch.setattr(main, "OWNED_SESSION_TTL_DAYS", 90)
    ahora = time.time()
    _sesion(dirs, "antigua", owner="ana", creada=ahora - 200 * 86400)
    _sesion(dirs, "reciente", owner="ana", creada=ahora - 2 * 86400)
    main._cleanup_old_sessions()
    quedan = sorted(p.name for p in dirs.iterdir())
    assert quedan == ["reciente"], quedan


def test_la_cuota_por_defecto_no_borra_nada_visible(dirs, monkeypatch):
    """SESSION_MAX_PER_USER debe cubrir lo que la galería muestra: /mine
    devuelve out[:60], así que con 60 nunca se borra algo que el usuario
    pueda ver. Si alguien baja el default, esto avisa."""
    assert main.SESSION_MAX_PER_USER >= 60


def test_los_lotes_siguen_caducando(dirs, monkeypatch):
    monkeypatch.setattr(main, "BATCH_TTL_HOURS", 24)
    import os
    b = dirs / "_batch"
    for nombre, edad in (("viejo", 48 * 3600), ("nuevo", 3600)):
        d = b / nombre
        d.mkdir(parents=True)
        (d / "x.zip").write_bytes(b"\0" * 1024)
        os.utime(d, (time.time() - edad, time.time() - edad))
    main._cleanup_old_sessions()
    assert sorted(p.name for p in b.iterdir()) == ["nuevo"]


def test_no_revienta_con_json_corrupto(dirs):
    d = dirs / "roto"
    (d / "products").mkdir(parents=True)
    (d / "bodegon.json").write_text("{no es json", encoding="utf-8")
    main._cleanup_old_sessions()      # no debe lanzar


def test_el_loop_periodico_dispara_de_verdad(dirs, monkeypatch):
    """La limpieza corría UNA sola vez, al arrancar, y un servidor de
    producción puede pasar semanas sin reiniciarse: no llegaba nunca justo
    cuando más hacía falta. Esta prueba existe porque un `asyncio.create_task`
    dentro del lifespan es fácil de escribir de forma que nunca ejecute.
    """
    import asyncio

    monkeypatch.setattr(main, "CLEANUP_EVERY_HOURS", 1 / 3600)   # 1 segundo
    monkeypatch.setattr(main, "SESSION_MAX_PER_USER", 1)
    monkeypatch.setattr(main, "SESSION_MAX_MB_PER_USER", 10_000)
    ahora = time.time()
    for i in range(4):
        _sesion(dirs, f"s{i}", owner="ana", creada=ahora - i * 60)

    async def correr():
        tarea = asyncio.create_task(main._cleanup_loop())
        await asyncio.sleep(2.5)
        quedan = len(list(dirs.iterdir()))
        tarea.cancel()
        try:
            await tarea
        except asyncio.CancelledError:
            pass
        return quedan

    assert asyncio.run(correr()) == 1


def test_un_fallo_no_mata_el_loop(dirs, monkeypatch):
    """Si la limpieza revienta una vez, el loop tiene que seguir vivo: si no,
    un error transitorio de disco deja la retención apagada para siempre."""
    import asyncio

    monkeypatch.setattr(main, "CLEANUP_EVERY_HOURS", 1 / 3600)
    llamadas = []

    def explota():
        llamadas.append(1)
        raise OSError("disco ocupado")

    monkeypatch.setattr(main, "_cleanup_old_sessions", explota)

    async def correr():
        tarea = asyncio.create_task(main._cleanup_loop())
        await asyncio.sleep(3.5)
        vivo = not tarea.done()
        tarea.cancel()
        try:
            await tarea
        except asyncio.CancelledError:
            pass
        return vivo

    vivo = asyncio.run(correr())
    assert len(llamadas) >= 2, f"solo se llamó {len(llamadas)} vez"
    assert vivo, "el loop murió con el primer fallo"
