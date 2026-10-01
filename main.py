"""
PSD Smart Object Replacer — FastAPI backend
Módulo 1: Reemplaza SmartObjectLayers en archivos PSD/PSB.
Módulo 2: Generador de composiciones con layout automático + propuesta IA.
"""
import hashlib
import io
import asyncio
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import threading
import time
import urllib.parse
import webbrowser
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional
import uuid

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from layout import compute_layout_any, compute_layout_ai_freeform
from renderer import get_image_dimensions, render_png, render_psd, STUDIO_BACKGROUND_STYLES


# ─── Configuración de seguridad (por variables de entorno) ────────────────────
def _envbool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


DEBUG = _envbool("DEBUG", False)
# Cookie Secure: actívalo en producción detrás de HTTPS
COOKIE_SECURE = _envbool("COOKIE_SECURE", False)
# Registro abierto (en producción conviene cerrarlo: invitaciones/aprobación)
REGISTRATION_OPEN = _envbool("REGISTRATION_OPEN", True)
# Orígenes permitidos por CORS (misma-origen por defecto)
ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get(
        "ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000"
    ).split(",") if o.strip()
]
# El Visor es donde una plantilla convertida se edita de verdad (mover capas,
# cambiar textos, exportar). Vive aparte, en su propio servidor, asi que Still
# solo necesita su direccion para poder mandarle la pieza de un clic: abre
# <VISOR_URL>?plantilla=<url del paquete> y el Visor se lo descarga solo.
#
# Sin configurar, el boton no aparece y queda el camino manual, que funciona
# igual: descargar el .still.json y subirlo alla.
VISOR_URL = os.environ.get("VISOR_URL", "").strip().rstrip("/")
# Cuanto vale el enlace que se le pasa al Visor. Es el tiempo de un clic, no el
# de una sesion: en cuanto caduca, el enlace no abre nada.
TRASPASO_TTL_SEG = int(os.environ.get("TRASPASO_TTL_SEG", "600"))
# Esa descarga es de origen cruzado y va con la cookie de sesion (el paquete es
# privado de su dueño), asi que el origen del Visor tiene que estar permitido
# por CORS. Se añade solo, para no obligar a configurar la misma direccion en
# dos variables distintas y que una se quede atras.
if VISOR_URL:
    _visor = urllib.parse.urlsplit(VISOR_URL)
    _origen_visor = f"{_visor.scheme}://{_visor.netloc}"
    if _origen_visor not in ALLOWED_ORIGINS:
        ALLOWED_ORIGINS.append(_origen_visor)

# Límites de subida
MAX_FILE_MB = int(os.environ.get("MAX_FILE_MB", "25"))
MAX_FILES = int(os.environ.get("MAX_FILES", "60"))
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024
# Un PSD de punto de venta no se parece a una foto de producto: el ejemplo
# del cliente pesa 65 MB y hay plantillas de 400. Con el limite de 25 MB de
# las fotos no entraria ninguna, asi que lleva el suyo.
MAX_PSD_MB = int(os.environ.get("MAX_PSD_MB", "300"))
MAX_PSD_BYTES = MAX_PSD_MB * 1024 * 1024
SESSION_TTL_HOURS = int(os.environ.get("SESSION_TTL_HOURS", "24"))
BATCH_TTL_HOURS = int(os.environ.get("BATCH_TTL_HOURS", "24"))

# Retención de las sesiones CON DUEÑO. Antes no tenían ninguna: la limpieza las
# salteaba enteras, así que crecían sin techo (medido: 1.4 GB en 47 sesiones) y
# en un filesystem con volumen eso llena el disco y tumba la app.
#
# El tope por cantidad está alineado con la galería: /api/bodegon/mine devuelve
# out[:60], o sea que a partir de la 61ª el usuario ya no puede verla. Guardar
# más es peso que nadie alcanza.
#
# El tope por tamaño es el que de verdad protege el disco: 60 sesiones pueden
# ser 200 MB o 8 GB según cuántos productos traiga cada una. Se borra de la más
# vieja hacia la más nueva hasta entrar en el presupuesto, así que la sesión en
# la que se está trabajando (la más nueva) nunca se toca.
SESSION_MAX_PER_USER = int(os.environ.get("SESSION_MAX_PER_USER", "60"))
SESSION_MAX_MB_PER_USER = int(os.environ.get("SESSION_MAX_MB_PER_USER", "1500"))
OWNED_SESSION_TTL_DAYS = int(os.environ.get("OWNED_SESSION_TTL_DAYS", "90"))
CLEANUP_EVERY_HOURS = float(os.environ.get("CLEANUP_EVERY_HOURS", "6"))
RENDER_RATE_LIMIT = int(os.environ.get("RENDER_RATE_LIMIT", "30"))
AI_RATE_LIMIT = int(os.environ.get("AI_RATE_LIMIT", "12"))
AI_IMAGE_RATE_LIMIT = int(os.environ.get("AI_IMAGE_RATE_LIMIT", "4"))


# ─── Rate limiting (ventana deslizante en memoria, sin dependencias) ──────────
_rl_lock = threading.Lock()
_rl_store: dict = {}


def _rate_limit(key: str, limit: int, window_s: int) -> bool:
    """Devuelve True si la petición se permite; False si se excedió el límite."""
    now = time.time()
    cutoff = now - window_s
    with _rl_lock:
        q = _rl_store.setdefault(key, [])
        while q and q[0] < cutoff:
            q.pop(0)
        if len(q) >= limit:
            return False
        q.append(now)
        return True


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_identity(request: Request) -> str:
    return request.session.get("user") or _client_ip(request)


def _require_rate_limit(request: Request, bucket: str, limit: int, window_s: int) -> None:
    if not _rate_limit(f"{bucket}:{_rate_identity(request)}", limit=limit, window_s=window_s):
        raise HTTPException(429, "Too many requests. Please wait a moment.")


# ─── Directorios ─────────────────────────────────────────────────────────────
# DATA_DIR: dónde vive todo lo que NO es código (sesiones, usuarios, secreto de
# cookies) — separado del código para poder montar un volumen persistente ahí
# en despliegues con filesystem efímero (ej. Railway) sin tocar el resto. Por
# defecto "." (mismo comportamiento de siempre en desarrollo local).
DATA_DIR = Path(os.environ.get("DATA_DIR", "."))
SESSIONS_DIR = DATA_DIR / "sessions"
STATIC_DIR = Path("static")
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(exist_ok=True)


# ─── Autenticación ────────────────────────────────────────────────────────────
USERS_FILE = DATA_DIR / "users.json"
SECRET_FILE = DATA_DIR / ".secret"

# Prefijos de rutas que requieren sesión iniciada
PROTECTED_PREFIXES = ("/api/bodegon", "/api/batch", "/api/convertidor")


def _get_secret() -> str:
    if SECRET_FILE.exists():
        return SECRET_FILE.read_text(encoding="utf-8").strip()
    s = secrets.token_hex(32)
    SECRET_FILE.write_text(s, encoding="utf-8")
    return s


def _load_users() -> dict:
    if USERS_FILE.exists():
        try:
            return json.loads(USERS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_users(users: dict) -> None:
    USERS_FILE.write_text(json.dumps(users, indent=2), encoding="utf-8")


def _hash_pw(pw: str, salt: bytes = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200_000)
    return f"{salt.hex()}:{dk.hex()}"


def _verify_pw(pw: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split(":")
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt_hex), 200_000)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


# ─── Helpers internos ─────────────────────────────────────────────────────────
def _dir_size(d: Path) -> int:
    """Bytes que ocupa un directorio, sin reventar si algo desaparece a mitad."""
    total = 0
    for root, _dirs, files in os.walk(d):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _cleanup_old_sessions() -> None:
    """Aplica la retención a sesiones anónimas, lotes y sesiones con dueño.

    Las tres tienen reglas distintas a propósito:

    - Anónimas: TTL corto (SESSION_TTL_HOURS). No hay a quién mostrárselas.
    - Lotes: TTL corto (BATCH_TTL_HOURS). Son un zip de paso.
    - Con dueño: son el trabajo del usuario, así que NO se borran por edad sola.
      Se aplican, en este orden, cuota de cantidad → presupuesto de disco →
      TTL largo de respaldo. Siempre de la más vieja a la más nueva.
    """
    ahora = time.time()
    session_cutoff = ahora - SESSION_TTL_HOURS * 3600
    batch_cutoff = ahora - BATCH_TTL_HOURS * 3600
    owned_cutoff = ahora - OWNED_SESSION_TTL_DAYS * 86400

    por_usuario: dict = {}
    borradas = 0

    for d in SESSIONS_DIR.iterdir():
        if not d.is_dir():
            continue
        if d.name == "_batch":
            for bd in d.iterdir():
                if bd.is_dir() and bd.stat().st_mtime < batch_cutoff:
                    shutil.rmtree(bd, ignore_errors=True)
                    borradas += 1
            continue

        info_file = d / "bodegon.json"
        info = {}
        if info_file.exists():
            try:
                info = json.loads(info_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                info = {}

        owner = info.get("owner")
        if not owner:
            # Anónima: como antes.
            if d.stat().st_mtime < session_cutoff:
                shutil.rmtree(d, ignore_errors=True)
                borradas += 1
            continue

        # Con dueño: se junta para decidir por usuario, no una por una.
        try:
            creada = float(info.get("created") or d.stat().st_mtime)
        except (TypeError, ValueError):
            creada = d.stat().st_mtime
        por_usuario.setdefault(owner, []).append((creada, d))

    presupuesto = SESSION_MAX_MB_PER_USER * 1024 * 1024
    for owner, sesiones in por_usuario.items():
        sesiones.sort(key=lambda x: x[0], reverse=True)      # más nueva primero

        sobran = sesiones[SESSION_MAX_PER_USER:]             # cuota de cantidad
        quedan = sesiones[:SESSION_MAX_PER_USER]

        # Presupuesto de disco: se suma desde la más nueva y se corta EN la que
        # se pasa, no después. Si se conservara la que desborda, el tope no
        # sería un tope: con un presupuesto de 1500 MB y una sesión de 235 MB
        # (tamaño real, medido) se podía terminar en 1735.
        #
        # La más nueva se conserva SIEMPRE, sin importar el presupuesto: es la
        # que el usuario tiene abierta, y borrarla por cuota sería borrarle el
        # trabajo en curso. Un presupuesto más chico que una sola sesión
        # degrada a "guardar solo la última", que es lo razonable.
        usado = 0
        for i, (_creada, d) in enumerate(quedan):
            usado += _dir_size(d)
            if usado > presupuesto and i > 0:
                sobran.extend(quedan[i:])
                break

        # TTL largo de respaldo, para cuentas abandonadas.
        sobran.extend((c, d) for c, d in quedan if c < owned_cutoff
                      and not any(d == x[1] for x in sobran))

        for _creada, d in sobran:
            _logger.info("Retention: deleting session %s of %s", d.name, owner)
            shutil.rmtree(d, ignore_errors=True)
            borradas += 1

    if borradas:
        _logger.info("Retention: %d sessions/batches deleted", borradas)


def _safe_name(name: str) -> str:
    name = Path(name).name
    name = re.sub(r"[^\w\-_. ]", "_", name)
    return (name[:255] or "file")


def _valid_uuid(value: str) -> bool:
    return bool(re.match(r"^[0-9a-f\-]{36}$", value or ""))


def _load_session_info(session_id: str, request: Request) -> tuple[Path, Path, dict]:
    """Carga una sesión y verifica que sea del usuario actual."""
    if not _valid_uuid(session_id):
        raise HTTPException(400, "Invalid session ID")
    session_dir = SESSIONS_DIR / session_id
    info_file = session_dir / "bodegon.json"
    if not info_file.exists():
        raise HTTPException(404, "Session not found")
    try:
        info = json.loads(info_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        raise HTTPException(404, "Session not found")
    if not _owned_by(info, request):
        raise HTTPException(404, "Session not found")
    return session_dir, info_file, info


def _public_error(message: str, exc: Exception) -> HTTPException:
    """Evita filtrar paths o detalles de SDKs cuando DEBUG está apagado."""
    _logger.exception("%s: %s", message, exc)
    detail = f"{message}: {exc}" if DEBUG else message
    return HTTPException(500, detail)


# ─── Lifespan ─────────────────────────────────────────────────────────────────
async def _cleanup_loop() -> None:
    """Corre la retención cada CLEANUP_EVERY_HOURS mientras la app viva.

    Antes solo corría al arrancar, y un servidor de producción puede pasar
    semanas sin reiniciarse: la limpieza no llegaba nunca justo cuando más
    hacía falta. Va en threadpool porque recorre el disco y bloquea.
    """
    while True:
        # Piso de 1 s, no de 15 min: lo unico que tiene que evitar el piso es
        # un busy-loop si alguien configura 0. Un piso grande hacia el loop
        # imposible de verificar en una prueba, que es peor negocio — la
        # proteccion real es que el default son 6 h.
        await asyncio.sleep(max(1.0, CLEANUP_EVERY_HOURS * 3600))
        try:
            await run_in_threadpool(_cleanup_old_sessions)
        except Exception:                      # nunca matar el loop por un fallo
            _logger.exception("Periodic session cleanup failed")


@asynccontextmanager
async def lifespan(app_: FastAPI):
    _cleanup_old_sessions()
    tarea = asyncio.create_task(_cleanup_loop())

    def _open_browser():
        time.sleep(1.5)
        webbrowser.open("http://localhost:8000")

    threading.Thread(target=_open_browser, daemon=True).start()
    try:
        yield
    finally:
        tarea.cancel()


# ─── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(title="Still by Ravel", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,      # misma-origen por defecto (no "*")
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

# Content-Security-Policy (permite lo que la app necesita: Tailwind local, Google Fonts,
# imágenes data:/blob: y código inline). 'unsafe-inline' es necesario por el JS/CSS inline.
# Plugins: herramientas de terceros que se abren DENTRO de Still, en un iframe.
# Son las unicas excepciones a default-src 'self', y se listan una por una a
# proposito: un frame-src abierto dejaria incrustar cualquier cosa.
#
# Lo que se acepta al hacer esto: el codigo de esos dominios corre dentro de
# nuestra pagina. No puede leer nuestro DOM ni nuestras cookies —son otro
# origen y el navegador los aisla— pero si podria intentar navegar la pestana
# entera a otro sitio. Eso se corta en el HTML con el sandbox del iframe, que
# NO concede allow-top-navigation.
# Se usan las URL CANONICAS, no las de un despliegue concreto. La de Promos
# apuntaba antes a
# https://6aa1c2baebddef319658c42c--legendary-pie-3f4d8e.netlify.app, que
# lleva el hash del deploy delante: eso clava el plugin a ESA version y no ve
# ninguna actualizacion posterior. Con la canonica sigue siempre al ultimo
# deploy, que es lo que se quiere de un plugin.
#
# Aqui solo van los plugins de TERCEROS. PDF no esta y no tiene que estar: es
# nuestro y se sirve desde el propio Still (/static/plugins/pdf.html), asi que
# el 'self' de frame-src ya lo cubre. La contrapartida de esa decision es que
# la CSP de Still tambien gobierna al plugin: con connect-src 'self' e
# img-src sin dominios externos, su funcion de "descargar imagenes por URL"
# no puede funcionar y hay que arrastrar los archivos. Se acepto a sabiendas
# —esa descarga ya fallaba contra Pinterest por CORS— a cambio de no montar
# otro despliegue ni abrir un tercer origen.
PLUGINS_PERMITIDOS = (
    "https://automatizador-plantillas-production.up.railway.app "
    "https://legendary-pie-3f4d8e.netlify.app"
)

# Los plugins propios viven bajo /static/plugins/ y se abren DENTRO de Still,
# en un iframe. La politica general dice frame-ancestors 'none' —que nadie nos
# incruste— y resulta que eso tambien nos impide incrustarnos a NOSOTROS
# mismos: el navegador bloquea el marco aunque el padre sea del mismo origen.
# Se comprobo en el navegador: suelto el plugin arranca, dentro del iframe el
# marco no llega a cargar.
#
# Por eso esa carpeta, y solo esa, lleva frame-ancestors 'self': se puede
# incrustar desde Still y desde ningun otro sitio. Todo lo demas —la app, la
# API— sigue con 'none' y sin poder incrustarse en ninguna parte.
# Rutas cuyo contenido es NUESTRO y se abre dentro de Still en un iframe.
# Ademas de los plugins propios entra la vista previa del Convertidor, que
# es un documento HTML completo generado a partir del PSD.
_PLUGINS_PROPIOS = ("/static/plugins/", "/api/convertidor/plantilla/")

_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data: blob:; "
    "connect-src 'self'; "
    f"frame-src 'self' {PLUGINS_PERMITIDOS}; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)

_CSP_PLUGIN_PROPIO = _CSP.replace("frame-ancestors 'none'", "frame-ancestors 'self'")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    propio = request.url.path.startswith(_PLUGINS_PROPIOS)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "SAMEORIGIN" if propio else "DENY"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    resp.headers["Content-Security-Policy"] = _CSP_PLUGIN_PROPIO if propio else _CSP
    if COOKIE_SECURE:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    # El CSS compilado tiene que revalidar. StaticFiles manda ETag pero no
    # Cache-Control, y sin directivas el navegador puede servir su copia sin
    # preguntar: una hoja vieja deja la interfaz sin las utilidades nuevas
    # (pb-4, drop-shadow...) y el resultado es una pantalla descuadrada que
    # no se explica mirando el codigo. Con no-cache revalida por ETag, asi
    # que cuando no ha cambiado la respuesta es un 304 vacio.
    #
    # Las imagenes NO llevan esto a proposito: pesan mucho mas y cuando
    # cambian se cambia su nombre.
    if request.url.path.endswith(".css"):
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"

    return resp


@app.middleware("http")
async def auth_guard(request: Request, call_next):
    """Bloquea las rutas protegidas si no hay sesión iniciada."""
    if request.url.path.startswith(PROTECTED_PREFIXES) and not request.session.get("user"):
        return JSONResponse({"detail": "Sign in to continue"}, status_code=401)
    return await call_next(request)


# La sesión (cookie firmada) debe envolver al guard → se agrega de último (outermost)
app.add_middleware(
    SessionMiddleware,
    secret_key=_get_secret(),
    same_site="lax",
    https_only=COOKIE_SECURE,    # Secure en producción (HTTPS)
    max_age=60 * 60 * 24 * 7,    # 7 días
)

_logger = logging.getLogger("stillai")


@app.exception_handler(Exception)
async def _unhandled_exception(request: Request, exc: Exception):
    """Evita filtrar detalles internos: registra el error y responde genérico."""
    _logger.exception("Unhandled error in %s", request.url.path)
    if DEBUG:
        return JSONResponse({"detail": f"{type(exc).__name__}: {exc}"}, status_code=500)
    return JSONResponse({"detail": "Internal server error"}, status_code=500)


# ─── Rutas ────────────────────────────────────────────────────────────────────
# ─── Iconos en la RAÍZ ─────────────────────────────────────────────────────────
# El navegador pide /favicon.ico por su cuenta, sin mirar los <link> del HTML:
# al abrir la pestana, desde un marcador, desde el historial. Aqui devolvia 404
# y encima los navegadores CACHEAN ese 404, asi que la pestana se quedaba sin
# icono por mucho que el HTML declarara los suyos — y el ?v= de los <link> no
# sirve de nada contra una URL que el navegador construye solo.
#
# Lo mismo con los apple-touch-icon, que iOS pide en la raiz al anadir a la
# pantalla de inicio si no encuentra la etiqueta.
_ICONOS_RAIZ = {
    "/favicon.ico": ("favicon.ico", "image/x-icon"),
    "/apple-touch-icon.png": ("favicon-180.png", "image/png"),
    "/apple-touch-icon-precomposed.png": ("favicon-180.png", "image/png"),
}


def _servir_icono_raiz(nombre: str, tipo: str) -> FileResponse:
    ruta = STATIC_DIR / nombre
    if not ruta.exists():
        raise HTTPException(404, "Icon not found")
    # Estos si se guardan: son pequenos y cuando cambian se cambia su nombre
    # o su ?v= en el HTML.
    return FileResponse(
        str(ruta),
        media_type=tipo,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/favicon.ico", include_in_schema=False)
async def favicon_raiz() -> FileResponse:
    return _servir_icono_raiz(*_ICONOS_RAIZ["/favicon.ico"])


@app.get("/apple-touch-icon.png", include_in_schema=False)
async def apple_icono() -> FileResponse:
    return _servir_icono_raiz(*_ICONOS_RAIZ["/apple-touch-icon.png"])


@app.get("/apple-touch-icon-precomposed.png", include_in_schema=False)
async def apple_icono_pre() -> FileResponse:
    return _servir_icono_raiz(*_ICONOS_RAIZ["/apple-touch-icon-precomposed.png"])


@app.get("/", response_class=HTMLResponse)
async def frontend() -> HTMLResponse:
    """La pagina entera. Toda la aplicacion vive en este HTML.

    Va con no-cache por una razon concreta: esta respuesta no llevaba NINGUNA
    directiva de cache, y sin directivas el navegador aplica cache heuristica
    y puede servir su copia guardada sin preguntar. El efecto es que despues
    de desplegar te sigues viendo la version anterior — paso justo con el
    logo: la marca aparecia como un cuadrado con una "S" de texto, que es
    como se dibujaba antes de tener la imagen real.

    no-cache no significa "no guardes": significa "pregunta antes de usar".
    El navegador conserva la copia y revalida con el ETag, asi que si el HTML
    no ha cambiado la respuesta es un 304 vacio. Es una peticion por carga,
    a cambio de no volver a servir una interfaz vieja.

    Los ficheros de /static (imagenes, CSS) no llevan esto: los sirve
    StaticFiles con su propio ETag y ahi si conviene que se guarden.
    """
    html_path = STATIC_DIR / "index.html"
    if not html_path.exists():
        raise HTTPException(404, "Frontend not found")
    return HTMLResponse(
        html_path.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


@app.get("/api/status")
async def status():
    return {"ok": True}


# ─── Auth ─────────────────────────────────────────────────────────────────────
@app.post("/api/auth/register")
async def auth_register(request: Request, body: dict):
    if not REGISTRATION_OPEN:
        raise HTTPException(403, "Registration is closed")
    if not _rate_limit(f"reg:{_client_ip(request)}", limit=5, window_s=3600):
        raise HTTPException(429, "Too many attempts. Please wait a moment.")
    u = (body.get("username") or "").strip().lower()
    p = body.get("password") or ""
    if len(u) < 3 or not re.match(r"^[a-z0-9_.\-]+$", u):
        raise HTTPException(400, "Invalid username (min 3; letters, numbers, . _ -)")
    if len(p) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    users = _load_users()
    if u in users:
        raise HTTPException(409, "That username already exists")
    users[u] = {"password": _hash_pw(p), "created": time.time()}
    _save_users(users)
    request.session["user"] = u          # inicia sesión automáticamente
    return {"username": u}


@app.post("/api/auth/login")
async def auth_login(request: Request, body: dict):
    # Anti fuerza-bruta: por IP y por usuario
    u = (body.get("username") or "").strip().lower()
    p = body.get("password") or ""
    if not _rate_limit(f"login:{_client_ip(request)}", limit=10, window_s=300) or \
       not _rate_limit(f"login_u:{u}", limit=8, window_s=300):
        raise HTTPException(429, "Too many sign-in attempts. Please wait a few minutes.")
    users = _load_users()
    if u not in users or not _verify_pw(p, users[u]["password"]):
        raise HTTPException(401, "Incorrect username or password")
    request.session["user"] = u
    return {"username": u}


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    request.session.clear()
    return {"ok": True}


AVATARS_DIR = DATA_DIR / "avatars"
MAX_AVATAR_MB = 5
AVATAR_PX = 256      # se guarda cuadrada y chica: es un avatar, no un asset


def _avatar_path(user: str) -> Path:
    """Ruta del avatar. Vive en DATA_DIR y NO en static/: en una plataforma de
    filesystem efimero, static/ va dentro de la imagen y un redeploy se lleva
    la foto — es el mismo error que ya costo los kits del modulo de Promos."""
    return AVATARS_DIR / f"{_safe_name(user)}.png"


def _perfil(user: str, users: dict = None) -> dict:
    """Lo que la interfaz necesita saber del usuario actual."""
    users = users if users is not None else _load_users()
    info = users.get(user) or {}
    return {
        "username": user,
        "display_name": info.get("display_name") or user,
        "has_avatar": _avatar_path(user).exists(),
    }


@app.get("/api/auth/me")
async def auth_me(request: Request):
    u = request.session.get("user")
    if not u:
        raise HTTPException(401, "Not authenticated")
    return _perfil(u)


@app.put("/api/auth/profile")
async def auth_profile(body: dict, request: Request):
    """Cambia el nombre VISIBLE, no el de acceso.

    El username es la clave de users.json y el `owner` de cada sesion en disco:
    renombrarlo obligaria a migrar todas las sesiones, y un fallo a mitad
    dejaria sesiones huerfanas sin dueno. El nombre visible es un campo aparte
    que no rompe nada.
    """
    u = request.session.get("user")
    if not u:
        raise HTTPException(401, "Not authenticated")
    nombre = (body.get("display_name") or "").strip()
    if not (1 <= len(nombre) <= 60):
        raise HTTPException(400, "Name must be between 1 and 60 characters")
    users = _load_users()
    if u not in users:
        raise HTTPException(404, "User not found")
    users[u]["display_name"] = nombre
    _save_users(users)
    return _perfil(u, users)


@app.post("/api/auth/password")
async def auth_password(body: dict, request: Request):
    """Cambia la contrasena. Exige la actual: sin eso, una sesion robada o un
    equipo desatendido bastarian para quedarse con la cuenta."""
    u = request.session.get("user")
    if not u:
        raise HTTPException(401, "Not authenticated")
    _require_rate_limit(request, "pwchange", 10, 3600)
    actual = body.get("current_password") or ""
    nueva = body.get("new_password") or ""
    users = _load_users()
    if u not in users or not _verify_pw(actual, users[u]["password"]):
        raise HTTPException(400, "Current password is incorrect")
    if len(nueva) < 8:
        raise HTTPException(400, "New password must be at least 8 characters")
    if _verify_pw(nueva, users[u]["password"]):
        raise HTTPException(400, "The new password is the same as the current one")
    users[u]["password"] = _hash_pw(nueva)
    _save_users(users)
    return {"ok": True}


@app.post("/api/auth/avatar")
async def auth_avatar_upload(request: Request, avatar: UploadFile = File(...)):
    """Sube la foto de perfil. Se normaliza a PNG cuadrado de AVATAR_PX: asi
    da igual que suban un JPEG de 8000px o un WebP raro, y el tamano en disco
    queda acotado por usuario sin necesidad de cuota aparte."""
    u = request.session.get("user")
    if not u:
        raise HTTPException(401, "Not authenticated")
    _require_rate_limit(request, "avatar", 20, 3600)
    data = await avatar.read()
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > MAX_AVATAR_MB * 1024 * 1024:
        raise HTTPException(413, f"Over the {MAX_AVATAR_MB} MB limit")
    # A un hilo, como el render y las miniaturas: recortar y redimensionar es
    # trabajo de CPU y en un handler async correria en el bucle de eventos.
    # Aqui es una sola imagen, no noventa, pero es el mismo patron y no hay
    # razon para dejar la excepcion.
    try:
        await run_in_threadpool(_normalizar_avatar, data, _avatar_path(u))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Could not read the image ({e})")
    return _perfil(u)


def _normalizar_avatar(data: bytes, destino: Path) -> None:
    """Recorte central cuadrado a AVATAR_PX y guardado como PNG.

    Aparte para poder mandarla a un hilo con run_in_threadpool.
    """
    from PIL import Image

    im = Image.open(io.BytesIO(data))
    im.load()
    im = im.convert("RGBA")
    lado = min(im.size)                      # recorte central cuadrado
    izq, arr = (im.width - lado) // 2, (im.height - lado) // 2
    im = im.crop((izq, arr, izq + lado, arr + lado))
    im = im.resize((AVATAR_PX, AVATAR_PX), Image.LANCZOS)
    AVATARS_DIR.mkdir(parents=True, exist_ok=True)
    im.save(destino, "PNG", optimize=True)


@app.get("/api/auth/avatar")
async def auth_avatar_get(request: Request):
    u = request.session.get("user")
    if not u:
        raise HTTPException(401, "Not authenticated")
    fp = _avatar_path(u)
    if not fp.exists():
        raise HTTPException(404, "No avatar")
    return FileResponse(str(fp), media_type="image/png")


@app.delete("/api/auth/avatar")
async def auth_avatar_delete(request: Request):
    u = request.session.get("user")
    if not u:
        raise HTTPException(401, "Not authenticated")
    _avatar_path(u).unlink(missing_ok=True)
    return _perfil(u)


# ══════════════════════════════════════════════════════════════════════════════
# BODEGÓN GENERATOR
# ══════════════════════════════════════════════════════════════════════════════

VALID_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".gif"}

# Propuestas IA visora: la IA decide orden, héroe, énfasis y plantilla; Pillow
# resuelve la geometría final para mantener todos los productos legibles.
AI_VARIANT_DEFS = [
    ("ai_dinamica",  "AI vision — Dynamic",   "Energy and ad-style hierarchy", "dynamic"),
    ("ai_elegante",  "AI vision — Elegant",   "Premium order, clean read", "elegant"),
    ("ai_editorial", "AI vision — Editorial", "Asymmetric rhythm with breathing room", "editorial"),
]
AI_STRATEGY_IDS = {vid for vid, _, _, _ in AI_VARIANT_DEFS}
AI_VARIANT_STYLES = {vid: style for vid, _, _, style in AI_VARIANT_DEFS}
AI_STYLE_DEFAULT_LAYOUT = {
    "dynamic": "impacto",
    "elegant": "profundidad",
    "editorial": "radial",
}
AI_LAYOUT_ALIASES = {
    "impacto": "ai_impacto",
    "profundidad": "ai_depth",
    "profundidad_grad": "ai_profundidad_grad",
    "fila_recta": "ai_fila_recta",
    "cruce": "ai_cruce",
    "cascada": "ai_cascada",
    "radial": "ai_radial",
    "flotante": "ai_flotante",
    "inclinado_derecha": "ai_inclinado_derecha",
    "inclinado_izquierda": "ai_inclinado_izquierda",
}


def _is_ai_strategy(sort_strategy: str) -> bool:
    """'ai_libre_1'/'ai_libre_2'/... son dinámicas (una por cada composición
    libre que devolvió la IA), no entradas fijas de AI_VARIANT_DEFS."""
    return sort_strategy in AI_STRATEGY_IDS or sort_strategy.startswith("ai_libre_")


def _layout_strategy_for_ai(sort_strategy: str, layout_choices: dict = None) -> str:
    style = AI_VARIANT_STYLES.get(sort_strategy)
    if not style:
        return sort_strategy
    choices = layout_choices if isinstance(layout_choices, dict) else {}
    selected = choices.get(style) or AI_STYLE_DEFAULT_LAYOUT[style]
    return AI_LAYOUT_ALIASES.get(selected, AI_LAYOUT_ALIASES[AI_STYLE_DEFAULT_LAYOUT[style]])


def _visual_ai_images(images: list, order: list, sizes: dict) -> list:
    """Aplica dirección IA sin permitir escalas que vuelvan ilegible un SKU."""
    by_name = {img["name"]: img for img in images}
    names = [name for name in order if name in by_name]
    names.extend(name for name in by_name if name not in names)
    result = []
    for name in names:
        img = dict(by_name[name])
        try:
            scale = float(sizes.get(name, 1.0))
        except (TypeError, ValueError):
            scale = 1.0
        ratio = max(1, float(img.get("width", 1))) / max(1, float(img.get("height", 1)))
        # Pisos de LEGIBILIDAD, no de uniformidad: antes eran 0.78/0.62 y
        # anulaban la escala real (un envase de 37 g salía igual que uno de
        # 473 ml). Ver ai_proposal.SIZE_FLOOR — deben moverse juntos.
        min_scale = 0.40 if ratio > 1.35 else 0.45
        scale = max(min_scale, min(1.0, scale))
        if ratio > 1.35:
            # Una bolsa horizontal puede ocupar tres veces el ancho de una
            # botella aun con la misma altura. Limitamos su masa visual, no su
            # legibilidad, para que no desplace al héroe fuera del conjunto.
            scale = min(scale, max(min_scale, 1.12 / ratio))
        img["scale"] = round(scale, 3)
        result.append(img)
    return result


def _owned_by(info: dict, request: Request) -> bool:
    """True si la sesión es del usuario actual, o si es una sesión vieja sin
    dueño (de antes de este campo) — nunca bloquea lo que ya funcionaba."""
    owner = info.get("owner")
    return owner is None or owner == request.session.get("user")


def _tilt_angle_from_cfg(cfg: dict):
    """Ángulo elegido por el usuario para Inclinado derecha/izquierda (slider
    en la UI). None si no vino — layout.py usa su default (30°) en ese caso."""
    val = cfg.get("tilt_angle")
    if val in (None, ""):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None

# Galería de fondos de estudio (IA, pre-generados) — id debe existir en
# renderer.STUDIO_BACKGROUND_STYLES. La miniatura vive en static/backgrounds/thumbs/.
BACKGROUND_STYLE_DEFS = [
    ("studio_dark", "Dark studio",    "Dark backdrop with a warm spot"),
    ("electric",    "Electric",       "Energy bolts, high impact"),
    ("splash",      "Splash",         "Two-tone liquid jets"),
]


@app.post("/api/bodegon/upload")
async def bodegon_upload(
    request: Request,
    products: List[UploadFile] = File(...),
    background: Optional[UploadFile] = File(default=None),
    product_names: Optional[List[str]] = Form(default=None),
):
    """Sube imágenes de productos y (opcionalmente) un fondo. Retorna dimensiones.

    product_names (opcional, mismo orden que 'products'): nombre que ya
    calculó el cliente para cada archivo — el frontend lo desambigua ahí
    (permite subir la misma foto dos veces sin que colapsen en un producto).
    Si no viene (p. ej. el flujo de Por lotes), se deriva del filename acá,
    con la misma desambiguación como respaldo.
    """
    if len(products) > MAX_FILES:
        raise HTTPException(413, f"Maximum {MAX_FILES} images per group")

    session_id = str(uuid.uuid4())
    session_dir = SESSIONS_DIR / session_id
    products_dir = session_dir / "products"
    products_dir.mkdir(parents=True)

    images = []
    skipped = []   # [{filename, reason}] — antes se descartaban en silencio; ahora se avisan
    seen_names: dict = {}   # red de seguridad: desambigua igual si algo llega repetido
    for i, f in enumerate(products):
        original_name = f.filename or f"file_{i + 1}"
        ext = Path(f.filename or "").suffix.lower()
        if ext not in VALID_IMAGE_EXTS:
            skipped.append({"filename": original_name, "reason": "Unsupported format"})
            continue

        data = await f.read()
        if len(data) > MAX_FILE_BYTES:
            skipped.append({"filename": original_name, "reason": f"Over the {MAX_FILE_MB} MB limit"})
            continue

        provided = product_names[i] if product_names and i < len(product_names) else None
        if provided:
            # El cliente ya calculó este nombre (stemUp() en el navegador) y
            # lo usa como clave para pintar los previews (BOD_FILES.find por
            # name) — hay que devolvérselo EXACTO, sin volver a normalizarlo,
            # o el nombre que ve el cliente y el que trae el layout del
            # servidor divergen apenas el nombre trae un carácter que
            # stemUp() no toca pero este regex sí (apóstrofes, paréntesis,
            # "&", "#"...). Ahí es donde antes se rompía el preview (quedaba
            # en blanco): BOD_FILES.find() nunca encontraba la imagen.
            base_name = provided.strip() or f"PRODUCT_{i}"
        else:
            raw_base = Path(_safe_name(f.filename or f"product_{i}{ext}")).stem
            base_name = re.sub(r"[^\w]", "_", raw_base.upper()).strip("_") or f"PRODUCT_{i}"
        count = seen_names.get(base_name, 0) + 1
        seen_names[base_name] = count
        # 1ª vez: nombre tal cual. 2ª+: sufijo _2, _3... — así dos fotos con
        # el mismo nombre (o la misma foto subida dos veces) no se pisan
        # entre sí en disco ni colapsan en un solo producto.
        name = base_name if count == 1 else f"{base_name}_{count}"
        # El nombre "lógico" (name, arriba) puede traer caracteres que no
        # sirven como nombre de archivo — para el disco se sanea aparte,
        # sin tocar el que se guarda en images[]/se manda a la IA/se
        # compara en el preview.
        disk_stem = re.sub(r"[^\w]", "_", name.upper()).strip("_") or f"PRODUCT_{i}"
        safe = f"{disk_stem}{ext}"
        dest = products_dir / safe
        dup = 1
        while dest.exists():
            dup += 1
            dest = products_dir / f"{disk_stem}__{dup}{ext}"
            safe = dest.name
        dest.write_bytes(data)

        try:
            w, h = get_image_dimensions(str(dest))
        except Exception as e:
            dest.unlink(missing_ok=True)
            skipped.append({"filename": original_name, "reason": f"Could not read the image ({e})"})
            continue

        images.append({
            "name": name,
            "filename": safe,
            "filepath": str(dest),
            "width": w,
            "height": h,
        })

    if not images:
        shutil.rmtree(session_dir, ignore_errors=True)
        raise HTTPException(400, "No valid images found")

    # Fondo opcional
    bg_path = None
    if background and background.filename:
        ext = Path(background.filename).suffix.lower()
        bg_data = await background.read()
        if ext in VALID_IMAGE_EXTS and len(bg_data) <= MAX_FILE_BYTES:
            bg_safe = _safe_name(background.filename)
            bg_dest = session_dir / bg_safe
            bg_dest.write_bytes(bg_data)
            bg_path = str(bg_dest)

    info = {
        "session_id": session_id,
        "images": images,
        "background_path": bg_path,
        "owner": request.session.get("user"),
        "created": time.time(),
    }
    (session_dir / "bodegon.json").write_text(json.dumps(info, indent=2), encoding="utf-8")

    return {
        "session_id": session_id,
        "count": len(images),
        "images": [
            {"name": img["name"], "filename": img["filename"],
             "width": img["width"], "height": img["height"]}
            for img in images
        ],
        "has_background": bg_path is not None,
        "skipped": skipped,   # [{filename, reason}] — antes desaparecían sin avisar
    }


@app.post("/api/bodegon/compute")
async def bodegon_compute(body: dict, request: Request):
    """Calcula el layout y retorna las posiciones de cada producto."""
    session_id = body.get("session_id", "")
    _session_dir, info_file, info = _load_session_info(session_id, request)

    cfg = body.get("config", {})
    layout = compute_layout_any(
        images=info["images"],
        sort_strategy=cfg.get("sort_strategy", "auto"),
        rows_count=int(cfg.get("rows_count", 0)),
        base_height=int(cfg.get("base_height", 760)),
        item_gap=int(cfg.get("item_gap", 40)),
        internal_padding=int(cfg.get("internal_padding", 120)),
        max_vertical_boost=float(cfg.get("max_vertical_boost", 1.20)),
        aspect_w=int(cfg.get("aspect_w", 0)),
        aspect_h=int(cfg.get("aspect_h", 0)),
        tilt_angle=_tilt_angle_from_cfg(cfg),
    )

    info["layout"] = layout
    info_file.write_text(json.dumps(info, indent=2), encoding="utf-8")

    return layout


def _resolve_render_params(body: dict, info: dict, info_file: Path):
    """Resuelve (layout, background_style, zoom_detail, shadow_enabled) a
    partir del body + la sesión — misma lógica para /render (PNG) y
    /render-psd, extraída para no duplicar el manejo de estrategias
    (ai_custom, ai_libre_N, geométricas) en los dos endpoints."""
    # Re-computar si hay config nueva en el body
    if "config" in body:
        cfg = body["config"]
        # Para las estrategias de IA usamos las imágenes ordenadas + escaladas
        # que persistió /ai-proposal
        strategy_id = cfg.get("sort_strategy", "auto")
        layout_strategy_id = _layout_strategy_for_ai(strategy_id, info.get("ai_layout_choices"))
        if _is_ai_strategy(strategy_id) and info.get("ai_images"):
            render_images = info["ai_images"]
        else:
            render_images = info["images"]
        if strategy_id == "ai_custom":
            # Composición generada por /api/bodegon/ai-custom-layout — trae
            # su propio set de imágenes con scale (la IA de esa llamada
            # aislada estima sus propios tamaños, no reusa los de /ai-proposal).
            custom = info.get("ai_custom_layout") or {}
            if not custom.get("layout"):
                raise HTTPException(400, "No custom composition has been generated yet")
            if custom.get("strategy"):
                layout = compute_layout_any(
                    images=custom["images"],
                    sort_strategy=AI_LAYOUT_ALIASES.get(custom["strategy"], custom["strategy"]),
                    hero_name=custom.get("hero"),
                    rows_count=int(cfg.get("rows_count", 0)),
                    base_height=int(cfg.get("base_height", 760)),
                    item_gap=int(cfg.get("item_gap", 40)),
                    internal_padding=int(cfg.get("internal_padding", 120)),
                    max_vertical_boost=1.0,
                    aspect_w=int(cfg.get("aspect_w", 0)),
                    aspect_h=int(cfg.get("aspect_h", 0)),
                    tilt_angle=_tilt_angle_from_cfg(cfg),
                )
            else:
                # Compatibilidad con composiciones libres guardadas antes del
                # cambio a plantillas dirigidas.
                layout = compute_layout_ai_freeform(
                    images=custom["images"],
                    free_layout=custom["layout"],
                    base_height=int(cfg.get("base_height", 760)),
                    max_vertical_boost=float(cfg.get("max_vertical_boost", 1.20)),
                )
        elif strategy_id.startswith("ai_libre_"):
            options = info.get("ai_free_layout_options") or []
            idx = int(strategy_id.rsplit("_", 1)[-1]) - 1
            free_layout = options[idx]["layout"] if 0 <= idx < len(options) else None
            layout = compute_layout_ai_freeform(
                images=render_images,
                free_layout=free_layout,
                base_height=int(cfg.get("base_height", 760)),
                max_vertical_boost=(
                    1.0 if _is_ai_strategy(strategy_id)
                    else float(cfg.get("max_vertical_boost", 1.20))
                ),
            )
        else:
            layout = compute_layout_any(
                images=render_images,
                sort_strategy=layout_strategy_id,
                rows_count=int(cfg.get("rows_count", 0)),
                base_height=int(cfg.get("base_height", 760)),
                item_gap=int(cfg.get("item_gap", 40)),
                internal_padding=int(cfg.get("internal_padding", 120)),
                max_vertical_boost=(
                    1.0 if _is_ai_strategy(strategy_id)
                    else float(cfg.get("max_vertical_boost", 1.20))
                ),
                aspect_w=int(cfg.get("aspect_w", 0)),
                aspect_h=int(cfg.get("aspect_h", 0)),
                tilt_angle=_tilt_angle_from_cfg(cfg),
                hero_name=info.get("ai_hero") if _is_ai_strategy(strategy_id) else None,
            )
        background_style = cfg.get("background_style") or None
        zoom_detail = None
        if cfg.get("zoom_detail"):
            is_ai = _is_ai_strategy(strategy_id)
            zoom_detail = {
                "target": info.get("ai_hero") if is_ai else None,
                "region": info.get("ai_detail_crop") if is_ai else None,
            }
        shadow_enabled = bool(cfg.get("shadow", True))
        # Persistir el layout renderizado — es la única propuesta que /compute
        # NO alcanza a guardar sola: las tarjetas ✦ IA (incluida ai_libre_N) se
        # arman con el layout que ya trajo /ai-proposal, sin volver a llamar a
        # /compute. Guardarlo acá (además de en /compute) asegura que "la
        # última propuesta" quede igual sin importar qué camino se usó, para
        # que reabrir la sesión (Mis proyectos) muestre exactamente esa.
        info["layout"] = layout
        info_file.write_text(json.dumps(info, indent=2), encoding="utf-8")
    elif "layout" in info:
        layout = info["layout"]
        background_style = None
        zoom_detail = None
        shadow_enabled = True
    else:
        raise HTTPException(400, "Run /compute first")

    if background_style not in (None, "none") and background_style not in STUDIO_BACKGROUND_STYLES:
        raise HTTPException(400, "Invalid background style")

    return layout, background_style, zoom_detail, shadow_enabled


@app.post("/api/bodegon/render")
async def bodegon_render(body: dict, request: Request):
    """Renderiza la composición como PNG y la guarda en la sesión."""
    _require_rate_limit(request, "render", RENDER_RATE_LIMIT, 3600)
    session_id = body.get("session_id", "")
    session_dir, info_file, info = _load_session_info(session_id, request)
    layout, background_style, zoom_detail, shadow_enabled = _resolve_render_params(body, info, info_file)

    output_dir = session_dir / "output"
    output_dir.mkdir(exist_ok=True)
    out_path = output_dir / "bodegon.png"

    try:
        await run_in_threadpool(
            render_png,
            layout, str(out_path),
            info.get("background_path"),
            background_style if background_style != "none" else None,
            zoom_detail,
            True,
            shadow_enabled,
        )
    except Exception as e:
        raise _public_error("PNG render failed", e)

    return {"session_id": session_id, "filename": "bodegon.png"}


@app.post("/api/bodegon/render-psd")
async def bodegon_render_psd(body: dict, request: Request):
    """Renderiza la composición como PSD con cada producto en su propia capa
    editable (ver renderer.render_psd) y lo guarda en la sesión. No soporta
    zoom de detalle (se ignora si está activo — queda fuera del PSD)."""
    _require_rate_limit(request, "render_psd", RENDER_RATE_LIMIT, 3600)
    session_id = body.get("session_id", "")
    session_dir, info_file, info = _load_session_info(session_id, request)
    layout, background_style, _zoom_detail, shadow_enabled = _resolve_render_params(body, info, info_file)

    output_dir = session_dir / "output"
    output_dir.mkdir(exist_ok=True)
    out_path = output_dir / "bodegon.psd"

    try:
        await run_in_threadpool(
            render_psd,
            layout, str(out_path),
            info.get("background_path"),
            background_style if background_style != "none" else None,
            True,
            shadow_enabled,
        )
    except Exception as e:
        raise _public_error("PSD render failed", e)

    return {"session_id": session_id, "filename": "bodegon.psd"}


@app.post("/api/bodegon/ai-generate-image")
async def bodegon_ai_generate_image(body: dict, request: Request):
    """Genera la composición COMPLETA con IA generativa (gpt-image-2, images.edit)
    — modo alternativo y experimental al pipeline determinista: acá la IA
    redibuja la imagen final directamente a partir de las fotos de referencia
    (no un layout que después renderiza Pillow, sin tocar píxeles). Solo
    requiere OPENAI_API_KEY (es el único proveedor con este modelo). Ver
    image_gen.py."""
    _require_rate_limit(request, "ai_image", AI_IMAGE_RATE_LIMIT, 3600)
    session_id = body.get("session_id", "")
    session_dir, _info_file, info = _load_session_info(session_id, request)

    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not openai_key:
        raise HTTPException(503, "Set OPENAI_API_KEY in .env to use this mode")

    custom_prompt = (body.get("custom_prompt") or "").strip()

    # El tope se valida ACÁ, no dentro de generate_bodegon_image, porque no es
    # un fallo del servidor sino del pedido: pasar por el `except Exception` de
    # abajo lo convertía en un 500 y _public_error le borraba el mensaje con
    # DEBUG apagado, así que el usuario veía "AI image generation failed" y
    # nunca se enteraba del límite ni de que los otros modos SÍ aguantan su
    # cantidad. Mismo criterio que el 413 de MAX_FILES en /upload.
    from image_gen import MAX_PRODUCTS_FOR_GENERATION
    n = len(info["images"])
    if n > MAX_PRODUCTS_FOR_GENERATION:
        raise HTTPException(
            400,
            f"AI Generation redraws the whole scene and only keeps label "
            f"fidelity up to {MAX_PRODUCTS_FOR_GENERATION} products (you have "
            f"{n}). For this many, use \u00abLayout styles\u00bb or \u00abAI proposals\u00bb: "
            f"they compose your real photos without redrawing them, so nothing "
            f"loses sharpness.",
        )

    try:
        from image_gen import generate_bodegon_image
        # La llamada a OpenAI tarda 1-3 min y es síncrona (bloqueante) — sin
        # threadpool, congela el único event loop de uvicorn (un worker) y
        # ningún otro request (de este u otro usuario) se sirve mientras tanto.
        png_bytes = await run_in_threadpool(
            generate_bodegon_image, info["images"], openai_key, custom_prompt
        )
    except Exception as e:
        raise _public_error("AI image generation failed", e)

    output_dir = session_dir / "output"
    output_dir.mkdir(exist_ok=True)
    out_path = output_dir / "bodegon_ai.png"
    out_path.write_bytes(png_bytes)

    return {"session_id": session_id, "filename": "bodegon_ai.png"}


@app.get("/api/bodegon/ai-generated-file/{session_id}")
async def bodegon_ai_generated_file(session_id: str, request: Request):
    """Descarga/muestra el PNG generado por /ai-generate-image."""
    session_dir, _info_file, _info = _load_session_info(session_id, request)
    out = session_dir / "output" / "bodegon_ai.png"
    if not out.exists():
        raise HTTPException(404, "PNG not found. Generate it first.")
    return FileResponse(str(out), filename="composition_ai.png", media_type="image/png")


@app.get("/api/bodegon/ai-generated-psd/{session_id}")
async def bodegon_ai_generated_psd(session_id: str, request: Request):
    """Descarga el resultado de Generación IA como PSD de una sola capa
    (no hay productos separados que envolver en capas — ver
    image_gen.wrap_as_psd). Convierte al vuelo el PNG ya generado por
    /ai-generate-image, no dispara una nueva llamada a la IA."""
    session_dir, _info_file, _info = _load_session_info(session_id, request)
    src = session_dir / "output" / "bodegon_ai.png"
    if not src.exists():
        raise HTTPException(404, "PNG not found. Generate it first.")
    try:
        from image_gen import wrap_as_psd
        psd_bytes = wrap_as_psd(src.read_bytes())
    except Exception as e:
        raise _public_error("PSD generation failed", e)
    return Response(
        content=psd_bytes,
        media_type="image/vnd.adobe.photoshop",
        headers={"Content-Disposition": 'attachment; filename="composition_ai.psd"'},
    )


@app.get("/api/bodegon/background-styles")
async def bodegon_background_styles():
    """Lista la galería de fondos de estudio (IA, pre-generados) disponibles."""
    return {
        "styles": [
            {"id": sid, "label": label, "desc": desc, "thumb": f"/static/backgrounds/thumbs/{sid}.jpg"}
            for sid, label, desc in BACKGROUND_STYLE_DEFS
        ]
    }


@app.get("/api/bodegon/download/{session_id}")
async def bodegon_download(session_id: str, request: Request):
    """Descarga el PNG generado."""
    session_dir, _info_file, _info = _load_session_info(session_id, request)
    out = session_dir / "output" / "bodegon.png"
    if not out.exists():
        raise HTTPException(404, "PNG not found. Run the render first.")
    return FileResponse(str(out), filename="composition.png", media_type="image/png")


@app.get("/api/bodegon/download-psd/{session_id}")
async def bodegon_download_psd(session_id: str, request: Request):
    """Descarga el PSD generado (capas editables por producto)."""
    session_dir, _info_file, _info = _load_session_info(session_id, request)
    out = session_dir / "output" / "bodegon.psd"
    if not out.exists():
        raise HTTPException(404, "PSD not found. Run the render first.")
    return FileResponse(str(out), filename="composition.psd", media_type="image/vnd.adobe.photoshop")


@app.get("/api/bodegon/mine")
async def bodegon_mine(request: Request):
    """Lista las sesiones (grupos de productos) del usuario actual, más
    reciente primero — para la galería 'Mis proyectos' y para precargar
    automáticamente los últimos productos al entrar."""
    user = request.session.get("user")
    out = []
    for d in SESSIONS_DIR.iterdir():
        if not d.is_dir() or d.name == "_batch":
            continue
        info_file = d / "bodegon.json"
        if not info_file.exists():
            continue
        try:
            info = json.loads(info_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if info.get("owner") != user:   # a diferencia de _owned_by: acá NO se
            continue                     # listan las sesiones viejas sin dueño
        layout = info.get("layout") or {}
        out.append({
            "session_id": info.get("session_id", d.name),
            "created": info.get("created") or info_file.stat().st_mtime,
            "product_count": len(info.get("images", [])),
            "has_render": (d / "output" / "bodegon.png").exists(),
            "sort_strategy": layout.get("sort_strategy"),
            # Los primeros productos, para que una sesion sin renderizar pueda
            # ensenar sus fotos reales en la portada en vez de un hueco. El
            # campo que sirve es filename (name viene sin extension).
            "preview": [
                im["filename"]
                for im in (info.get("images") or [])[:3]
                if im.get("filename")
            ],
        })
    out.sort(key=lambda x: x["created"], reverse=True)
    return {"sessions": out[:60]}


@app.get("/api/bodegon/session/{session_id}")
async def bodegon_session_get(session_id: str, request: Request):
    """Devuelve el estado completo de una sesión propia para reabrirla (Mis
    composiciones / precarga automática): productos + la última propuesta
    guardada, si hay una."""
    _session_dir, _info_file, info = _load_session_info(session_id, request)
    return {
        "session_id": session_id,
        "images": [{"name": i["name"], "filename": i["filename"]} for i in info.get("images", [])],
        "layout": info.get("layout"),
    }


@app.get("/api/bodegon/product-file/{session_id}/{filename}")
async def bodegon_product_file(session_id: str, filename: str, request: Request):
    """Sirve la foto original de un producto ya subido (para repintar el
    preview en el navegador al reabrir una sesión propia — no requiere volver
    a subir nada)."""
    session_dir, _info_file, _info = _load_session_info(session_id, request)
    fp = session_dir / "products" / Path(filename).name   # .name neutraliza / y ..
    if not fp.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(str(fp))


THUMB_PX = 320       # miniatura de rejilla; nadie mira un borrador a 1600px


def _generar_miniatura(origen: Path, cache: Path) -> bool:
    """Crea la miniatura en disco. Devuelve False si no se pudo.

    Vive aparte para poder mandarla a un hilo con run_in_threadpool: es
    trabajo de CPU y no puede correr en el bucle de eventos.
    """
    from PIL import Image   # local, igual que en el avatar

    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        im = Image.open(origen)
        im = im.convert("RGBA")
        im.thumbnail((THUMB_PX, THUMB_PX), Image.LANCZOS)
        im.save(cache, "PNG", optimize=True)
        return True
    except (OSError, ValueError, Image.DecompressionBombError):
        return False


@app.get("/api/bodegon/product-thumb/{session_id}/{filename}")
async def bodegon_product_thumb(session_id: str, filename: str, request: Request):
    """Miniatura del producto, para las rejillas de portada y de proyectos.

    Existe por una medida concreta: pintar Projects con las fotos originales
    movia 51 MB (una sola imagen pesaba 5 MB) porque son los PNG de producto
    a resolucion completa. A 320 px eso baja a algo que cabe en una rejilla,
    y el resultado se guarda en disco para no volver a redimensionar.

    Aqui SI se reduce, al contrario que en la exportacion: esto no es un
    entregable, es una vista previa. La foto original no se toca.
    """
    session_dir, _info_file, _info = _load_session_info(session_id, request)
    origen = session_dir / "products" / Path(filename).name   # .name mata / y ..
    if not origen.exists():
        raise HTTPException(404, "File not found")

    cache = session_dir / "thumbs" / f"{origen.stem}_{THUMB_PX}.png"
    if not cache.exists() or cache.stat().st_mtime < origen.stat().st_mtime:
        # A UN HILO. Redimensionar con Pillow es trabajo de CPU, y un handler
        # async lo ejecuta EN el bucle de eventos: mientras dura, el servidor
        # entero deja de atender a nadie. Con la rejilla de proyectos no es
        # una miniatura sino noventa seguidas, asi que se nota.
        #
        # Medido en local con la cache vacia: sin hilo, una peticion trivial
        # pasaba de 3 ms a 143 ms de mediana (357 ms el peor caso) durante la
        # rafaga. Es el mismo run_in_threadpool que ya usan el render y el PSD.
        if not await run_in_threadpool(_generar_miniatura, origen, cache):
            # Si no se pudo miniaturizar, se sirve el original antes que dejar
            # el hueco que esto viene a quitar.
            return FileResponse(str(origen))

    return FileResponse(
        str(cache),
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@app.post("/api/bodegon/ai-proposal")
async def bodegon_ai_proposal(body: dict, request: Request):
    """Analiza los productos con Claude Vision y devuelve el orden óptimo + layout."""
    session_id = body.get("session_id", "")
    _session_dir, info_file, info = _load_session_info(session_id, request)
    cfg = body.get("config", {})

    cached = bool(info.get("ai_images"))
    if cached and not body.get("force"):
        ordered = _visual_ai_images(
            info["ai_images"],
            [img["name"] for img in info["ai_images"]],
            {img["name"]: img.get("scale", 1.0) for img in info["ai_images"]},
        )
        cached_names = [img["name"] for img in ordered]
        cached_hero = info.get("ai_hero", "")
        if cached_hero not in cached_names:
            cached_hero = cached_names[0] if cached_names else ""
        info["ai_images"] = ordered
        info["ai_hero"] = cached_hero
        info_file.write_text(json.dumps(info, indent=2), encoding="utf-8")
        suggestion = {
            "order": cached_names,
            "hero": cached_hero,
            "sizes": {img["name"]: img.get("scale", 1.0) for img in ordered},
            "layouts": info.get("ai_layout_choices", {}),
            "detail_crop": info.get("ai_detail_crop"),
            "reasoning": info.get("ai_reasoning", ""),
        }
    else:
        _require_rate_limit(request, "ai_proposal", AI_RATE_LIMIT, 3600)

        groq_key = os.environ.get("GROQ_API_KEY", "").strip()
        gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
        provider = os.environ.get("AI_PROVIDER", "").strip().lower() or None
        if not (groq_key or gemini_key or anthropic_key or openai_key):
            raise HTTPException(503, "Set GROQ_API_KEY, GEMINI_API_KEY, ANTHROPIC_API_KEY or OPENAI_API_KEY in .env")

        try:
            from ai_proposal import analyze_products
            suggestion = await run_in_threadpool(
                analyze_products,
                info["images"],
                groq_key or None,
                gemini_key or None,
                anthropic_key or None,
                openai_key or None,
                provider,
            )
        except Exception as e:
            raise _public_error("AI analysis failed", e)

        ordered = _visual_ai_images(
            info["images"], suggestion["order"], suggestion.get("sizes", {})
        )

        # Persistir la dirección de arte para que miniatura, PNG y PSD usen la
        # misma jerarquía y plantilla.
        info["ai_images"] = ordered
        info["ai_hero"] = suggestion.get("hero", "")
        info["ai_layout_choices"] = suggestion.get("layouts", {})
        info["ai_detail_crop"] = suggestion.get("detail_crop")
        info["ai_reasoning"] = suggestion.get("reasoning", "")
        info_file.write_text(json.dumps(info, indent=2), encoding="utf-8")

    # Varias propuestas IA desde el MISMO análisis (orden + tamaños), distinto layout
    def _ai_layout(strategy_id: str):
        return compute_layout_any(
            images=ordered,
            sort_strategy=_layout_strategy_for_ai(strategy_id, suggestion.get("layouts")),
            hero_name=suggestion.get("hero"),
            rows_count=int(cfg.get("rows_count", 0)),
            base_height=int(cfg.get("base_height", 760)),
            item_gap=int(cfg.get("item_gap", 40)),
            internal_padding=int(cfg.get("internal_padding", 120)),
            max_vertical_boost=1.0,
            aspect_w=int(cfg.get("aspect_w", 0)),
            aspect_h=int(cfg.get("aspect_h", 0)),
            tilt_angle=_tilt_angle_from_cfg(cfg),
        )

    variants = [
        {"id": vid, "label": label, "desc": desc, "layout": _ai_layout(vid)}
        for vid, label, desc, _style in AI_VARIANT_DEFS
    ]

    return {
        "variants": variants,
        "layout": variants[0]["layout"],   # compat
        "suggestion": suggestion,
    }


@app.post("/api/bodegon/ai-custom-layout")
async def bodegon_ai_custom_layout(body: dict, request: Request):
    """Composición dirigida por una instrucción libre del usuario ('quiero
    algo minimalista, fondo oscuro...') — llamada de IA AISLADA, separada de
    /ai-proposal: la instrucción del usuario es el único criterio que recibe
    esa llamada, sin competir con order/hero/sizes/los 3 estilos fijos (ver
    ai_proposal.analyze_custom_layout). Se suma como una tarjeta extra en
    'Propuestas IA'; no reemplaza nada de lo que ya devolvió /ai-proposal."""
    _require_rate_limit(request, "ai_custom", AI_RATE_LIMIT, 3600)
    session_id = body.get("session_id", "")
    _session_dir, info_file, info = _load_session_info(session_id, request)
    custom_prompt = (body.get("custom_prompt") or "").strip()
    if not custom_prompt:
        # Sin texto -> "generá una propuesta más, a tu criterio" (mismo botón
        # sirve para pedir algo puntual O simplemente para variedad extra
        # además de las 3 composiciones libres de /ai-proposal).
        custom_prompt = ("Propose a composition different from the ones you would give "
                          "by default, using your own art-director judgement.")

    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    provider = os.environ.get("AI_PROVIDER", "").strip().lower() or None
    if not (groq_key or gemini_key or anthropic_key or openai_key):
        raise HTTPException(503, "Set GROQ_API_KEY, GEMINI_API_KEY, ANTHROPIC_API_KEY or OPENAI_API_KEY in .env")

    try:
        from ai_proposal import analyze_custom_layout
        result = await run_in_threadpool(
            analyze_custom_layout,
            info["images"],
            custom_prompt,
            groq_key or None,
            gemini_key or None,
            anthropic_key or None,
            openai_key or None,
            provider,
        )
    except Exception as e:
        raise _public_error("AI analysis failed", e)

    images_with_scale = _visual_ai_images(
        info["images"], result["order"], result["sizes"]
    )

    cfg = body.get("config", {})
    layout = compute_layout_any(
        images=images_with_scale,
        sort_strategy=AI_LAYOUT_ALIASES.get(result["strategy"], result["strategy"]),
        hero_name=result["hero"],
        rows_count=int(cfg.get("rows_count", 0)),
        base_height=int(cfg.get("base_height", 760)),
        item_gap=int(cfg.get("item_gap", 40)),
        internal_padding=int(cfg.get("internal_padding", 120)),
        max_vertical_boost=1.0,
        aspect_w=int(cfg.get("aspect_w", 0)),
        aspect_h=int(cfg.get("aspect_h", 0)),
        tilt_angle=_tilt_angle_from_cfg(cfg),
    )

    # Se guarda aparte de ai_free_layout_options — un solo "a medida" activo
    # a la vez (cada click en "Generar con mi idea" reemplaza al anterior,
    # no se acumulan tarjetas infinitas).
    info["ai_custom_layout"] = {
        "label": result["label"],
        "layout": layout,
        "strategy": result["strategy"],
        "hero": result["hero"],
        "images": images_with_scale,
    }
    info_file.write_text(json.dumps(info, indent=2), encoding="utf-8")

    return {
        "id": "ai_custom",
        "label": f"IA · A medida — {result['label']}",
        "desc": "Art direction from your own description",
        "layout": layout,
    }


# ══════════════════════════════════════════════════════════════════════════════
# MÓDULO: CONVERTIDOR (un PSD de produccion -> una plantilla HTML)
#
# Las plantillas llegan como las nombra un disenador —"49", "2x", "SUAJE"— y no
# con una convencion legible por codigo. El modulo las lee, decide con ayuda de
# IA que es cada capa, y devuelve una plantilla HTML con un hueco editable por
# elemento variable. Ver convertidor.py para el detalle de los tres pasos.
#
# Esta ruta hace todo el trabajo DENTRO de la peticion: leer el PSD, llamar a la
# IA y aplanar el fondo suman entre 5 y 30 segundos segun el archivo. Sirve para
# probar en local; el dia que esto se llame desde fuera hay que partirlo en
# trabajo asincrono, igual que la generacion de imagen.
# ══════════════════════════════════════════════════════════════════════════════
CONVERTIDOR_DIR = SESSIONS_DIR / "_convertidor"


def _convertir_psd(ruta_psd: Path, destino: Path, nombre: str) -> dict:
    """Los tres pasos del convertidor. Aparte para poder mandarla a un hilo:
    psd-tools y Pillow son CPU pura y bloquearian el bucle de eventos."""
    import convertidor

    clave = os.environ.get("OPENAI_API_KEY", "").strip() or None
    html, estructura, rasters = convertidor.convertir(ruta_psd, clave, nombre)
    (destino / "plantilla.html").write_text(html, encoding="utf-8")
    (destino / "estructura.json").write_text(
        json.dumps(estructura, indent=2, ensure_ascii=False), encoding="utf-8")
    # El paquete se escribe ahora y no al descargarlo: rehacerlo despues
    # obligaria a volver a leer el PSD y a pagar otra llamada a la IA.
    (destino / "paquete.json").write_text(
        json.dumps(convertidor.paquete(estructura, rasters), ensure_ascii=False),
        encoding="utf-8")
    return estructura


@app.post("/api/convertidor/subir")
async def convertidor_subir(request: Request, archivo: UploadFile = File(...)):
    """Recibe un PSD y devuelve el resumen de la plantilla extraida."""
    _require_rate_limit(request, "convertidor", 20, 3600)

    if Path(archivo.filename or "").suffix.lower() not in (".psd", ".psb"):
        raise HTTPException(400, "Only .psd and .psb files are supported")

    datos = await archivo.read()
    if not datos:
        raise HTTPException(400, "Empty file")
    if len(datos) > MAX_PSD_BYTES:
        raise HTTPException(413, f"Over the {MAX_PSD_MB} MB limit")

    pieza_id = str(uuid.uuid4())
    destino = CONVERTIDOR_DIR / pieza_id
    destino.mkdir(parents=True)
    ruta_psd = destino / "origen.psd"
    ruta_psd.write_bytes(datos)
    (destino / "meta.json").write_text(json.dumps({
        "owner": request.session.get("user"), "created": time.time(),
        "filename": archivo.filename,
    }, indent=2), encoding="utf-8")

    try:
        estructura = await run_in_threadpool(
            _convertir_psd, ruta_psd, destino, archivo.filename or "plantilla.psd")
    except Exception as e:
        shutil.rmtree(destino, ignore_errors=True)
        raise HTTPException(422, f"Could not read the template: {e}") from e

    huecos = [c for c in estructura["capas"] if c.get("editable")]
    return {
        "id": pieza_id,
        "visor": _enlace_visor(pieza_id, request),
        "archivo": estructura["archivo"],
        "lienzo": estructura["lienzo"],
        "asistido_por_ia": estructura.get("asistido_por_ia", False),
        "aviso_ia": estructura.get("aviso_ia"),
        "total_capas": len(estructura["capas"]),
        "variantes": estructura.get("variantes", []),
        "huecos": [{
            "nombre": c["nombre_limpio"], "rol": c["rol"], "clase": c["clase"],
            "confianza": c["confianza"], "porque": c.get("porque", ""),
            "capa_original": c["nombre"], "texto": c.get("texto"),
            "sku": c.get("sku"),
        } for c in sorted(huecos, key=lambda x: (x["caja"]["y"], x["caja"]["x"]))],
    }


def _firmar_traspaso(pieza_id: str) -> str:
    """Permiso corto y firmado para que el Visor recoja UN paquete.

    El Visor vive en otro dominio. Lo natural seria que pidiera el paquete con
    la cookie de sesion, pero esa cookie es SameSite=lax a proposito: entre
    dominios distintos el navegador no la manda, y ponerla en SameSite=None
    seria bajarle la guardia anti-CSRF a TODO Still a cambio de una sola
    funcion. Mal negocio.

    Asi que no viaja la sesion: viaja esto. Una firma que solo sirve para esta
    pieza, que caduca en minutos y que no abre ninguna otra cosa de Still. Si
    el enlace se filtra, lo que se filtro es una plantilla durante diez
    minutos, no una cuenta.
    """
    exp = int(time.time()) + TRASPASO_TTL_SEG
    firma = hmac.new(_get_secret().encode(), f"{pieza_id}|{exp}".encode(),
                     hashlib.sha256).hexdigest()[:32]
    return f"{pieza_id}.{exp}.{firma}"


def _leer_traspaso(token: str) -> str:
    """La pieza a la que da acceso el token. Levanta si no es valido."""
    partes = (token or "").split(".")
    if len(partes) != 3:
        raise HTTPException(403, "Bad link")
    pieza_id, exp, firma = partes
    if not re.fullmatch(r"[0-9a-f-]{36}", pieza_id) or not exp.isdigit():
        raise HTTPException(403, "Bad link")
    esperada = hmac.new(_get_secret().encode(), f"{pieza_id}|{exp}".encode(),
                        hashlib.sha256).hexdigest()[:32]
    # compare_digest y no ==: comparar firmas byte a byte filtra por el tiempo
    # de respuesta cuantos caracteres acerto quien lo intenta.
    if not hmac.compare_digest(firma, esperada):
        raise HTTPException(403, "Bad link")
    if int(exp) < time.time():
        raise HTTPException(410, "This link expired. Send it to the Viewer again")
    return pieza_id


def _enlace_visor(pieza_id: str, request: Request) -> Optional[str]:
    """Enlace que abre esta pieza ya cargada en el Visor, o None si no hay Visor.

    Lleva la direccion del paquete, no el paquete: el Visor lo recoge el solo
    con el permiso firmado que va dentro de esa direccion.
    """
    if not VISOR_URL:
        return None
    paquete = str(request.url_for(
        "convertidor_traspaso", token=_firmar_traspaso(pieza_id)))
    return f"{VISOR_URL}?plantilla={urllib.parse.quote(paquete, safe='')}"


def _pieza_convertidor(pieza_id: str, request: Request) -> Path:
    """Carpeta de una conversion, comprobando que sea de quien pregunta."""
    if not re.fullmatch(r"[0-9a-f-]{36}", pieza_id or ""):
        raise HTTPException(400, "Bad id")
    destino = CONVERTIDOR_DIR / pieza_id
    meta = destino / "meta.json"
    if not meta.exists():
        raise HTTPException(404, "Not found")
    duenyo = json.loads(meta.read_text(encoding="utf-8")).get("owner")
    if duenyo and duenyo != request.session.get("user"):
        raise HTTPException(403, "Not yours")
    return destino


@app.get("/api/convertidor/plantilla/{pieza_id}", response_class=HTMLResponse)
async def convertidor_plantilla(pieza_id: str, request: Request):
    """La plantilla HTML. Se abre en un iframe dentro de Still, por eso esta
    ruta lleva frame-ancestors 'self' (ver _PLUGINS_PROPIOS)."""
    html = _pieza_convertidor(pieza_id, request) / "plantilla.html"
    if not html.exists():
        raise HTTPException(404, "Not found")
    return HTMLResponse(html.read_text(encoding="utf-8"),
                        headers={"Cache-Control": "no-store"})


_SALIDAS = {
    # nombre -> (archivo, tipo MIME, extension del archivo que se descarga)
    "html":    ("plantilla.html",  "text/html",        "html"),
    "paquete": ("paquete.json",    "application/json", "still.json"),
}


@app.get("/api/traspaso/{token}")
async def convertidor_traspaso(token: str):
    """El paquete de una pieza, para que el Visor lo recoja.

    Cuelga de /api/traspaso y no de /api/convertidor a proposito: ese prefijo
    exige sesion iniciada (ver auth_guard) y aqui justamente no hay ninguna.
    Quien manda es el token, que ya dice de que pieza se trata.
    """
    pieza_id = _leer_traspaso(token)
    ruta = CONVERTIDOR_DIR / pieza_id / "paquete.json"
    if not ruta.exists():
        raise HTTPException(404, "Not found")
    return FileResponse(ruta, media_type="application/json",
                        filename=f"{pieza_id}.still.json")


@app.get("/api/convertidor/descargar/{pieza_id}/{formato}")
async def convertidor_descargar(pieza_id: str, formato: str, request: Request):
    """Los cuatro formatos de salida.

    html y paquete ya estan escritos desde la conversion. psd y pdf se generan
    la primera vez que se piden y se guardan: ambos vuelven a leer el PSD a
    resolucion real, que es lo caro, y nadie los pide una sola vez.

    El paquete (.still.json) es el formato de intercambio: un unico archivo con
    la pieza entera —geometria, orden de pintado, tipografia y los pixeles de
    cada capa— para subirlo a donde haga falta.
    """
    destino = _pieza_convertidor(pieza_id, request)
    meta = json.loads((destino / "meta.json").read_text(encoding="utf-8"))
    base = re.sub(r"[^\w.-]+", "_", Path(meta.get("filename") or "plantilla").stem)

    if formato in _SALIDAS:
        archivo, mime, ext = _SALIDAS[formato]
        ruta = destino / archivo
        if not ruta.exists():
            raise HTTPException(404, "Not generated")
        return FileResponse(ruta, media_type=mime, filename=f"{base}.{ext}")

    if formato not in ("psd", "pdf"):
        raise HTTPException(400, "Unknown format")

    ruta = destino / f"pieza.{formato}"
    if not ruta.exists():
        import convertidor
        estructura = json.loads(
            (destino / "estructura.json").read_text(encoding="utf-8"))
        fn = convertidor.a_psd if formato == "psd" else convertidor.a_pdf
        try:
            await run_in_threadpool(fn, destino / "origen.psd", estructura, ruta)
        except Exception as e:
            raise HTTPException(422, f"Could not build the {formato.upper()}: {e}") from e

    mime = "image/vnd.adobe.photoshop" if formato == "psd" else "application/pdf"
    return FileResponse(ruta, media_type=mime, filename=f"{base}.{formato}")


@app.get("/api/convertidor/estructura/{pieza_id}")
async def convertidor_estructura(pieza_id: str, request: Request):
    """El JSON completo: toda capa, no solo los huecos."""
    ruta = _pieza_convertidor(pieza_id, request) / "estructura.json"
    if not ruta.exists():
        raise HTTPException(404, "Not found")
    return FileResponse(ruta, media_type="application/json",
                        filename="estructura.json")


# ══════════════════════════════════════════════════════════════════════════════
# MÓDULO 3: GENERACIÓN POR LOTES (varias carpetas -> varias piezas)
# ══════════════════════════════════════════════════════════════════════════════
def _empaquetar_zip(destino: Path, ficheros: list) -> None:
    """Escribe el ZIP. Aparte para poder mandarla a un hilo: comprimir es CPU
    y no puede correr en el bucle de eventos."""
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as zf:
        for ruta, nombre in ficheros:
            zf.write(str(ruta), nombre)


@app.post("/api/batch/zip")
async def batch_zip(body: dict, request: Request):
    """Empaqueta en un ZIP los PNG ya renderizados de varias sesiones (grupos)."""
    items = body.get("items", [])
    if not items:
        raise HTTPException(400, "No groups provided")

    batch_id = str(uuid.uuid4())
    batch_dir = SESSIONS_DIR / "_batch" / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    zip_path = batch_dir / "piezas.zip"

    # Primero se resuelve QUE va dentro: comprobar propietario y existencia es
    # barato y necesita el request, asi que se hace aqui.
    used: set = set()
    a_empaquetar: list = []
    for it in items:
        sid = it.get("session_id", "")
        try:
            session_dir, _info_file, _info = _load_session_info(sid, request)
        except HTTPException:
            continue
        png = session_dir / "output" / "bodegon.png"
        if not png.exists():
            continue
        base = _safe_name(it.get("name") or sid).rsplit(".", 1)[0]
        arc = f"{base}.png"
        n = 1
        while arc in used:
            arc = f"{base}_{n}.png"
            n += 1
        used.add(arc)
        a_empaquetar.append((png, arc))

    # Y comprimir va A UN HILO. Deflactar varios PNG de megabytes es trabajo de
    # CPU, y un handler async lo hace EN el bucle de eventos: mientras dura, el
    # servidor no atiende a nadie mas. Medido con solo 7 piezas, una peticion
    # trivial pasaba de 5 ms a 272 ms; un lote de 30 congelaria mas de un
    # segundo.
    if a_empaquetar:
        await run_in_threadpool(_empaquetar_zip, zip_path, a_empaquetar)

    if not used:
        shutil.rmtree(batch_dir, ignore_errors=True)
        raise HTTPException(404, "No rendered pieces to package")

    (batch_dir / "batch.json").write_text(
        json.dumps({"owner": request.session.get("user"), "created": time.time()}, indent=2),
        encoding="utf-8",
    )
    return {"batch_id": batch_id, "count": len(used)}


@app.get("/api/batch/download/{batch_id}")
async def batch_download(batch_id: str, request: Request):
    if not re.match(r"^[0-9a-f\-]{36}$", batch_id):
        raise HTTPException(400, "Invalid batch ID")
    batch_dir = SESSIONS_DIR / "_batch" / batch_id
    meta_file = batch_dir / "batch.json"
    if not meta_file.exists():
        raise HTTPException(404, "ZIP not found")
    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        raise HTTPException(404, "ZIP not found")
    if meta.get("owner") != request.session.get("user"):
        raise HTTPException(404, "ZIP not found")
    z = batch_dir / "piezas.zip"
    if not z.exists():
        raise HTTPException(404, "ZIP not found")
    # El fichero en disco sigue llamandose piezas.zip a proposito: los lotes
    # ya creados se buscan por ese nombre. Lo que cambia es el nombre con el
    # que llega al usuario.
    return FileResponse(str(z), filename="pieces.zip", media_type="application/zip")


app.mount("/static", StaticFiles(directory="static"), name="static")
