"""
PSD Smart Object Replacer — FastAPI backend
Módulo 1: Reemplaza SmartObjectLayers en archivos PSD/PSB.
Módulo 2: Generador de bodegones con layout automático + propuesta IA.
"""
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import threading
import time
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
# Límites de subida
MAX_FILE_MB = int(os.environ.get("MAX_FILE_MB", "25"))
MAX_FILES = int(os.environ.get("MAX_FILES", "60"))
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024


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
PROTECTED_PREFIXES = ("/api/bodegon", "/api/batch")


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
def _cleanup_old_sessions() -> None:
    cutoff = time.time() - 86_400  # 24 horas
    for d in SESSIONS_DIR.iterdir():
        if d.is_dir() and d.stat().st_mtime < cutoff:
            shutil.rmtree(d, ignore_errors=True)


def _safe_name(name: str) -> str:
    name = Path(name).name
    name = re.sub(r"[^\w\-_. ]", "_", name)
    return (name[:255] or "file")


# ─── Lifespan ─────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app_: FastAPI):
    _cleanup_old_sessions()

    def _open_browser():
        time.sleep(1.5)
        webbrowser.open("http://localhost:8000")

    threading.Thread(target=_open_browser, daemon=True).start()
    yield


# ─── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(title="StillAI", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,      # misma-origen por defecto (no "*")
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

# Content-Security-Policy (permite lo que la app necesita: Tailwind local, Google Fonts,
# imágenes data:/blob: y código inline). 'unsafe-inline' es necesario por el JS/CSS inline.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data: blob:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    resp.headers["Content-Security-Policy"] = _CSP
    if COOKIE_SECURE:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return resp


@app.middleware("http")
async def auth_guard(request: Request, call_next):
    """Bloquea las rutas protegidas si no hay sesión iniciada."""
    if request.url.path.startswith(PROTECTED_PREFIXES) and not request.session.get("user"):
        return JSONResponse({"detail": "Inicia sesión para continuar"}, status_code=401)
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
    _logger.exception("Error no controlado en %s", request.url.path)
    if DEBUG:
        return JSONResponse({"detail": f"{type(exc).__name__}: {exc}"}, status_code=500)
    return JSONResponse({"detail": "Error interno del servidor"}, status_code=500)


# ─── Rutas ────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def frontend() -> HTMLResponse:
    html_path = STATIC_DIR / "index.html"
    if not html_path.exists():
        raise HTTPException(404, "Frontend no encontrado")
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/api/status")
async def status():
    return {"ok": True}


# ─── Auth ─────────────────────────────────────────────────────────────────────
@app.post("/api/auth/register")
async def auth_register(request: Request, body: dict):
    if not REGISTRATION_OPEN:
        raise HTTPException(403, "El registro está cerrado")
    if not _rate_limit(f"reg:{_client_ip(request)}", limit=5, window_s=3600):
        raise HTTPException(429, "Demasiados intentos. Espera un momento.")
    u = (body.get("username") or "").strip().lower()
    p = body.get("password") or ""
    if len(u) < 3 or not re.match(r"^[a-z0-9_.\-]+$", u):
        raise HTTPException(400, "Usuario inválido (mín 3; letras, números, . _ -)")
    if len(p) < 8:
        raise HTTPException(400, "La contraseña debe tener al menos 8 caracteres")
    users = _load_users()
    if u in users:
        raise HTTPException(409, "Ese usuario ya existe")
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
        raise HTTPException(429, "Demasiados intentos de inicio de sesión. Espera unos minutos.")
    users = _load_users()
    if u not in users or not _verify_pw(p, users[u]["password"]):
        raise HTTPException(401, "Usuario o contraseña incorrectos")
    request.session["user"] = u
    return {"username": u}


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/auth/me")
async def auth_me(request: Request):
    u = request.session.get("user")
    if not u:
        raise HTTPException(401, "No autenticado")
    return {"username": u}


# ══════════════════════════════════════════════════════════════════════════════
# BODEGÓN GENERATOR
# ══════════════════════════════════════════════════════════════════════════════

VALID_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".gif"}

# Propuestas IA: mismo análisis (orden + tamaños), distinto tratamiento de layout
# Solo quedan las variantes donde el orden/tamaño que decide la IA cambia
# realmente el resultado (el héroe se escala/centra distinto). Se sacaron
# ai_shadow/ai_fila_recta/ai_cruce/ai_inclinado_* porque su fórmula geométrica
# es fija — con la IA se veían casi idénticas a la versión gratis, sin ningún
# valor visible a cambio de gastar la llamada de IA. Las composiciones
# "IA · Libre" (varias, con criterio propio de la IA) se arman aparte, ver
# free_layout_options en bodegon_ai_proposal — esas sí muestran una IA
# decidiendo de verdad, no alimentando una fórmula nuestra.
AI_VARIANT_DEFS = [
    ("ai",             "IA · Equilibrado",  "Orden y tamaños inteligentes"),
    ("ai_depth",       "IA · Profundidad",  "Héroe al frente + solapamiento"),
    ("ai_impacto",     "IA · Impacto",      "Abanico dinámico tipo anuncio"),
]
AI_STRATEGY_IDS = {vid for vid, _, _ in AI_VARIANT_DEFS}


def _is_ai_strategy(sort_strategy: str) -> bool:
    """'ai_libre_1'/'ai_libre_2'/... son dinámicas (una por cada composición
    libre que devolvió la IA), no entradas fijas de AI_VARIANT_DEFS."""
    return sort_strategy in AI_STRATEGY_IDS or sort_strategy.startswith("ai_libre_")


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
    ("studio_dark", "Estudio oscuro", "Fondo oscuro con spot cálido"),
    ("electric",    "Eléctrico",      "Rayos de energía, alto impacto"),
    ("splash",      "Splash",         "Chorros de líquido a dos colores"),
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
        raise HTTPException(413, f"Máximo {MAX_FILES} imágenes por grupo")

    session_id = str(uuid.uuid4())
    session_dir = SESSIONS_DIR / session_id
    products_dir = session_dir / "products"
    products_dir.mkdir(parents=True)

    images = []
    skipped = []   # [{filename, reason}] — antes se descartaban en silencio; ahora se avisan
    seen_names: dict = {}   # red de seguridad: desambigua igual si algo llega repetido
    for i, f in enumerate(products):
        original_name = f.filename or f"archivo_{i + 1}"
        ext = Path(f.filename or "").suffix.lower()
        if ext not in VALID_IMAGE_EXTS:
            skipped.append({"filename": original_name, "reason": "Formato no soportado"})
            continue

        data = await f.read()
        if len(data) > MAX_FILE_BYTES:
            skipped.append({"filename": original_name, "reason": f"Supera el máximo de {MAX_FILE_MB} MB"})
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
            skipped.append({"filename": original_name, "reason": f"No se pudo leer la imagen ({e})"})
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
        raise HTTPException(400, "No se encontraron imágenes válidas")

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
async def bodegon_compute(body: dict):
    """Calcula el layout y retorna las posiciones de cada producto."""
    session_id = body.get("session_id", "")
    if not re.match(r"^[0-9a-f\-]{36}$", session_id):
        raise HTTPException(400, "Session ID inválido")

    session_dir = SESSIONS_DIR / session_id
    info_file = session_dir / "bodegon.json"
    if not info_file.exists():
        raise HTTPException(404, "Sesión no encontrada")

    info = json.loads(info_file.read_text(encoding="utf-8"))

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
                raise HTTPException(400, "No hay una composición a medida generada todavía")
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
                max_vertical_boost=float(cfg.get("max_vertical_boost", 1.20)),
            )
        else:
            layout = compute_layout_any(
                images=render_images,
                sort_strategy=strategy_id,
                rows_count=int(cfg.get("rows_count", 0)),
                base_height=int(cfg.get("base_height", 760)),
                item_gap=int(cfg.get("item_gap", 40)),
                internal_padding=int(cfg.get("internal_padding", 120)),
                max_vertical_boost=float(cfg.get("max_vertical_boost", 1.20)),
                aspect_w=int(cfg.get("aspect_w", 0)),
                aspect_h=int(cfg.get("aspect_h", 0)),
                tilt_angle=_tilt_angle_from_cfg(cfg),
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
        # que reabrir la sesión (Mis bodegones) muestre exactamente esa.
        info["layout"] = layout
        info_file.write_text(json.dumps(info, indent=2), encoding="utf-8")
    elif "layout" in info:
        layout = info["layout"]
        background_style = None
        zoom_detail = None
        shadow_enabled = True
    else:
        raise HTTPException(400, "Ejecuta /compute primero")

    if background_style not in (None, "none") and background_style not in STUDIO_BACKGROUND_STYLES:
        raise HTTPException(400, "Estilo de fondo inválido")

    return layout, background_style, zoom_detail, shadow_enabled


@app.post("/api/bodegon/render")
async def bodegon_render(body: dict):
    """Renderiza el bodegón como PNG y lo guarda en la sesión."""
    session_id = body.get("session_id", "")
    if not re.match(r"^[0-9a-f\-]{36}$", session_id):
        raise HTTPException(400, "Session ID inválido")

    session_dir = SESSIONS_DIR / session_id
    info_file = session_dir / "bodegon.json"
    if not info_file.exists():
        raise HTTPException(404, "Sesión no encontrada")

    info = json.loads(info_file.read_text(encoding="utf-8"))
    layout, background_style, zoom_detail, shadow_enabled = _resolve_render_params(body, info, info_file)

    output_dir = session_dir / "output"
    output_dir.mkdir(exist_ok=True)
    out_path = output_dir / "bodegon.png"

    try:
        render_png(
            layout, str(out_path),
            background_path=info.get("background_path"),
            background_style=(background_style if background_style != "none" else None),
            zoom_detail=zoom_detail,
            shadow=shadow_enabled,
        )
    except Exception as e:
        raise HTTPException(500, f"Error renderizando PNG: {e}")

    return {"session_id": session_id, "filename": "bodegon.png"}


@app.post("/api/bodegon/render-psd")
async def bodegon_render_psd(body: dict):
    """Renderiza el bodegón como PSD con cada producto en su propia capa
    editable (ver renderer.render_psd) y lo guarda en la sesión. No soporta
    zoom de detalle (se ignora si está activo — queda fuera del PSD)."""
    session_id = body.get("session_id", "")
    if not re.match(r"^[0-9a-f\-]{36}$", session_id):
        raise HTTPException(400, "Session ID inválido")

    session_dir = SESSIONS_DIR / session_id
    info_file = session_dir / "bodegon.json"
    if not info_file.exists():
        raise HTTPException(404, "Sesión no encontrada")

    info = json.loads(info_file.read_text(encoding="utf-8"))
    layout, background_style, _zoom_detail, shadow_enabled = _resolve_render_params(body, info, info_file)

    output_dir = session_dir / "output"
    output_dir.mkdir(exist_ok=True)
    out_path = output_dir / "bodegon.psd"

    try:
        render_psd(
            layout, str(out_path),
            background_path=info.get("background_path"),
            background_style=(background_style if background_style != "none" else None),
            shadow=shadow_enabled,
        )
    except Exception as e:
        raise HTTPException(500, f"Error renderizando PSD: {e}")

    return {"session_id": session_id, "filename": "bodegon.psd"}


@app.post("/api/bodegon/ai-generate-image")
async def bodegon_ai_generate_image(body: dict):
    """Genera el bodegón COMPLETO con IA generativa (gpt-image-2, images.edit)
    — modo alternativo y experimental al pipeline determinista: acá la IA
    redibuja la imagen final directamente a partir de las fotos de referencia
    (no un layout que después renderiza Pillow, sin tocar píxeles). Solo
    requiere OPENAI_API_KEY (es el único proveedor con este modelo). Ver
    image_gen.py."""
    session_id = body.get("session_id", "")
    if not re.match(r"^[0-9a-f\-]{36}$", session_id):
        raise HTTPException(400, "Session ID inválido")

    session_dir = SESSIONS_DIR / session_id
    info_file = session_dir / "bodegon.json"
    if not info_file.exists():
        raise HTTPException(404, "Sesión no encontrada")

    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not openai_key:
        raise HTTPException(503, "Configura OPENAI_API_KEY en .env para usar este modo")

    info = json.loads(info_file.read_text(encoding="utf-8"))
    custom_prompt = (body.get("custom_prompt") or "").strip()

    try:
        from image_gen import generate_bodegon_image
        # La llamada a OpenAI tarda 1-3 min y es síncrona (bloqueante) — sin
        # threadpool, congela el único event loop de uvicorn (un worker) y
        # ningún otro request (de este u otro usuario) se sirve mientras tanto.
        png_bytes = await run_in_threadpool(
            generate_bodegon_image, info["images"], openai_key, custom_prompt
        )
    except Exception as e:
        raise HTTPException(500, f"Error generando imagen con IA: {e}")

    output_dir = session_dir / "output"
    output_dir.mkdir(exist_ok=True)
    out_path = output_dir / "bodegon_ai.png"
    out_path.write_bytes(png_bytes)

    return {"session_id": session_id, "filename": "bodegon_ai.png"}


@app.get("/api/bodegon/ai-generated-file/{session_id}")
async def bodegon_ai_generated_file(session_id: str, request: Request):
    """Descarga/muestra el PNG generado por /ai-generate-image."""
    if not re.match(r"^[0-9a-f\-]{36}$", session_id):
        raise HTTPException(400, "Session ID inválido")
    session_dir = SESSIONS_DIR / session_id
    info_file = session_dir / "bodegon.json"
    if info_file.exists():
        info = json.loads(info_file.read_text(encoding="utf-8"))
        if not _owned_by(info, request):
            raise HTTPException(404, "PNG no encontrado. Generalo primero.")
    out = session_dir / "output" / "bodegon_ai.png"
    if not out.exists():
        raise HTTPException(404, "PNG no encontrado. Generalo primero.")
    return FileResponse(str(out), filename="bodegon_ai.png", media_type="image/png")


@app.get("/api/bodegon/ai-generated-psd/{session_id}")
async def bodegon_ai_generated_psd(session_id: str, request: Request):
    """Descarga el resultado de Generación IA como PSD de una sola capa
    (no hay productos separados que envolver en capas — ver
    image_gen.wrap_as_psd). Convierte al vuelo el PNG ya generado por
    /ai-generate-image, no dispara una nueva llamada a la IA."""
    if not re.match(r"^[0-9a-f\-]{36}$", session_id):
        raise HTTPException(400, "Session ID inválido")
    session_dir = SESSIONS_DIR / session_id
    info_file = session_dir / "bodegon.json"
    if info_file.exists():
        info = json.loads(info_file.read_text(encoding="utf-8"))
        if not _owned_by(info, request):
            raise HTTPException(404, "PNG no encontrado. Generalo primero.")
    src = session_dir / "output" / "bodegon_ai.png"
    if not src.exists():
        raise HTTPException(404, "PNG no encontrado. Generalo primero.")
    try:
        from image_gen import wrap_as_psd
        psd_bytes = wrap_as_psd(src.read_bytes())
    except Exception as e:
        raise HTTPException(500, f"Error generando PSD: {e}")
    return Response(
        content=psd_bytes,
        media_type="image/vnd.adobe.photoshop",
        headers={"Content-Disposition": 'attachment; filename="bodegon_ai.psd"'},
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
    if not re.match(r"^[0-9a-f\-]{36}$", session_id):
        raise HTTPException(400, "Session ID inválido")
    session_dir = SESSIONS_DIR / session_id
    info_file = session_dir / "bodegon.json"
    if info_file.exists():
        info = json.loads(info_file.read_text(encoding="utf-8"))
        if not _owned_by(info, request):
            raise HTTPException(404, "PNG no encontrado. Ejecuta el render primero.")
    out = session_dir / "output" / "bodegon.png"
    if not out.exists():
        raise HTTPException(404, "PNG no encontrado. Ejecuta el render primero.")
    return FileResponse(str(out), filename="bodegon.png", media_type="image/png")


@app.get("/api/bodegon/download-psd/{session_id}")
async def bodegon_download_psd(session_id: str, request: Request):
    """Descarga el PSD generado (capas editables por producto)."""
    if not re.match(r"^[0-9a-f\-]{36}$", session_id):
        raise HTTPException(400, "Session ID inválido")
    session_dir = SESSIONS_DIR / session_id
    info_file = session_dir / "bodegon.json"
    if info_file.exists():
        info = json.loads(info_file.read_text(encoding="utf-8"))
        if not _owned_by(info, request):
            raise HTTPException(404, "PSD no encontrado. Ejecuta el render primero.")
    out = session_dir / "output" / "bodegon.psd"
    if not out.exists():
        raise HTTPException(404, "PSD no encontrado. Ejecuta el render primero.")
    return FileResponse(str(out), filename="bodegon.psd", media_type="image/vnd.adobe.photoshop")


@app.get("/api/bodegon/mine")
async def bodegon_mine(request: Request):
    """Lista las sesiones (grupos de productos) del usuario actual, más
    reciente primero — para la galería 'Mis bodegones' y para precargar
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
        })
    out.sort(key=lambda x: x["created"], reverse=True)
    return {"sessions": out[:60]}


@app.get("/api/bodegon/session/{session_id}")
async def bodegon_session_get(session_id: str, request: Request):
    """Devuelve el estado completo de una sesión propia para reabrirla (Mis
    bodegones / precarga automática): productos + la última propuesta
    guardada, si hay una."""
    if not re.match(r"^[0-9a-f\-]{36}$", session_id):
        raise HTTPException(400, "Session ID inválido")
    session_dir = SESSIONS_DIR / session_id
    info_file = session_dir / "bodegon.json"
    if not info_file.exists():
        raise HTTPException(404, "Sesión no encontrada")
    info = json.loads(info_file.read_text(encoding="utf-8"))
    if info.get("owner") != request.session.get("user"):
        raise HTTPException(404, "Sesión no encontrada")
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
    if not re.match(r"^[0-9a-f\-]{36}$", session_id):
        raise HTTPException(400, "Session ID inválido")
    session_dir = SESSIONS_DIR / session_id
    info_file = session_dir / "bodegon.json"
    if not info_file.exists():
        raise HTTPException(404, "Sesión no encontrada")
    info = json.loads(info_file.read_text(encoding="utf-8"))
    if info.get("owner") != request.session.get("user"):
        raise HTTPException(404, "Sesión no encontrada")
    fp = session_dir / "products" / Path(filename).name   # .name neutraliza / y ..
    if not fp.exists():
        raise HTTPException(404, "Archivo no encontrado")
    return FileResponse(str(fp))


@app.post("/api/bodegon/ai-proposal")
async def bodegon_ai_proposal(body: dict):
    """Analiza los productos con Claude Vision y devuelve el orden óptimo + layout."""
    session_id = body.get("session_id", "")
    if not re.match(r"^[0-9a-f\-]{36}$", session_id):
        raise HTTPException(400, "Session ID inválido")

    session_dir = SESSIONS_DIR / session_id
    info_file = session_dir / "bodegon.json"
    if not info_file.exists():
        raise HTTPException(404, "Sesión no encontrada")

    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    provider = os.environ.get("AI_PROVIDER", "").strip().lower() or None
    if not (groq_key or gemini_key or anthropic_key or openai_key):
        raise HTTPException(503, "Configura GROQ_API_KEY, GEMINI_API_KEY, ANTHROPIC_API_KEY u OPENAI_API_KEY en .env")

    info = json.loads(info_file.read_text(encoding="utf-8"))

    try:
        from ai_proposal import analyze_products
        suggestion = analyze_products(
            info["images"],
            groq_key=groq_key or None,
            gemini_key=gemini_key or None,
            anthropic_key=anthropic_key or None,
            openai_key=openai_key or None,
            provider=provider,
        )
    except Exception as e:
        raise HTTPException(500, f"Error en análisis IA: {e}")

    # Reordenar según la sugerencia de la IA y adjuntar el tamaño real relativo
    name_to_img = {img["name"]: img for img in info["images"]}
    sizes = suggestion.get("sizes", {})
    ordered = []
    for n in suggestion["order"]:
        if n in name_to_img:
            img = dict(name_to_img[n])
            img["scale"] = float(sizes.get(n, 1.0))
            ordered.append(img)

    # Persistir el orden + tamaños para que el render del PNG los respete,
    # + la región de zoom y la composición libre sugeridas (si el usuario
    # activa "Zoom de detalle" / elige la propuesta "IA · Libre")
    info["ai_images"] = ordered
    info["ai_hero"] = suggestion.get("hero", "")
    info["ai_detail_crop"] = suggestion.get("detail_crop")
    info["ai_free_layout_options"] = suggestion.get("free_layout_options", [])
    info_file.write_text(json.dumps(info, indent=2), encoding="utf-8")

    # Varias propuestas IA desde el MISMO análisis (orden + tamaños), distinto layout
    cfg = body.get("config", {})

    def _ai_layout(strategy_id: str):
        return compute_layout_any(
            images=ordered,
            sort_strategy=strategy_id,
            rows_count=int(cfg.get("rows_count", 0)),
            base_height=int(cfg.get("base_height", 760)),
            item_gap=int(cfg.get("item_gap", 40)),
            internal_padding=int(cfg.get("internal_padding", 120)),
            max_vertical_boost=float(cfg.get("max_vertical_boost", 1.20)),
            aspect_w=int(cfg.get("aspect_w", 0)),
            aspect_h=int(cfg.get("aspect_h", 0)),
            tilt_angle=_tilt_angle_from_cfg(cfg),
        )

    variants = [
        {"id": vid, "label": label, "desc": desc, "layout": _ai_layout(vid)}
        for vid, label, desc in AI_VARIANT_DEFS
    ]

    # "IA · Libre": una tarjeta por cada composición que propuso la IA (ver
    # free_layout_options en ai_proposal._build_prompt) — no una fórmula fija
    # nuestra, así que no pasa por compute_layout_any.
    for i, opt in enumerate(suggestion.get("free_layout_options", [])):
        variants.append({
            "id": f"ai_libre_{i + 1}",
            "label": f"IA · Libre — {opt['label']}",
            "desc": "Composición 100% propuesta por la IA",
            "layout": compute_layout_ai_freeform(
                images=ordered,
                free_layout=opt.get("layout"),
                base_height=int(cfg.get("base_height", 760)),
                max_vertical_boost=float(cfg.get("max_vertical_boost", 1.20)),
            ),
        })

    return {
        "variants": variants,
        "layout": variants[0]["layout"],   # compat
        "suggestion": suggestion,
    }


@app.post("/api/bodegon/ai-custom-layout")
async def bodegon_ai_custom_layout(body: dict):
    """Composición dirigida por una instrucción libre del usuario ('quiero
    algo minimalista, fondo oscuro...') — llamada de IA AISLADA, separada de
    /ai-proposal: la instrucción del usuario es el único criterio que recibe
    esa llamada, sin competir con order/hero/sizes/los 3 estilos fijos (ver
    ai_proposal.analyze_custom_layout). Se suma como una tarjeta extra en
    'Propuestas IA'; no reemplaza nada de lo que ya devolvió /ai-proposal."""
    session_id = body.get("session_id", "")
    if not re.match(r"^[0-9a-f\-]{36}$", session_id):
        raise HTTPException(400, "Session ID inválido")
    custom_prompt = (body.get("custom_prompt") or "").strip()
    if not custom_prompt:
        # Sin texto -> "generá una propuesta más, a tu criterio" (mismo botón
        # sirve para pedir algo puntual O simplemente para variedad extra
        # además de las 3 composiciones libres de /ai-proposal).
        custom_prompt = ("Proponé una composición distinta a las que ya darías por defecto, "
                          "con tu propio criterio artístico de director de arte.")

    session_dir = SESSIONS_DIR / session_id
    info_file = session_dir / "bodegon.json"
    if not info_file.exists():
        raise HTTPException(404, "Sesión no encontrada")

    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    provider = os.environ.get("AI_PROVIDER", "").strip().lower() or None
    if not (groq_key or gemini_key or anthropic_key or openai_key):
        raise HTTPException(503, "Configura GROQ_API_KEY, GEMINI_API_KEY, ANTHROPIC_API_KEY u OPENAI_API_KEY en .env")

    info = json.loads(info_file.read_text(encoding="utf-8"))

    try:
        from ai_proposal import analyze_custom_layout
        result = analyze_custom_layout(
            info["images"],
            custom_prompt,
            groq_key=groq_key or None,
            gemini_key=gemini_key or None,
            anthropic_key=anthropic_key or None,
            openai_key=openai_key or None,
            provider=provider,
        )
    except Exception as e:
        raise HTTPException(500, f"Error en análisis IA: {e}")

    # Igual que /ai-proposal: cada producto lleva su propio scale (acá, el
    # que estimó ESTA llamada aislada — no el de /ai-proposal, puede no
    # haberse ejecutado todavía).
    name_to_img = {img["name"]: img for img in info["images"]}
    sizes = result["sizes"]
    images_with_scale = []
    for n, img in name_to_img.items():
        img2 = dict(img)
        img2["scale"] = float(sizes.get(n, 1.0))
        images_with_scale.append(img2)

    cfg = body.get("config", {})
    layout = compute_layout_ai_freeform(
        images=images_with_scale,
        free_layout=result["layout"],
        base_height=int(cfg.get("base_height", 760)),
        max_vertical_boost=float(cfg.get("max_vertical_boost", 1.20)),
    )

    # Se guarda aparte de ai_free_layout_options — un solo "a medida" activo
    # a la vez (cada click en "Generar con mi idea" reemplaza al anterior,
    # no se acumulan tarjetas infinitas).
    info["ai_custom_layout"] = {
        "label": result["label"],
        "layout": result["layout"],
        "images": images_with_scale,
    }
    info_file.write_text(json.dumps(info, indent=2), encoding="utf-8")

    return {
        "id": "ai_custom",
        "label": f"IA · Libre — {result['label']}",
        "desc": "Composición 100% según tu descripción",
        "layout": layout,
    }


# ══════════════════════════════════════════════════════════════════════════════
# MÓDULO 3: GENERACIÓN POR LOTES (varias carpetas → varios bodegones)
# ══════════════════════════════════════════════════════════════════════════════
@app.post("/api/batch/zip")
async def batch_zip(body: dict):
    """Empaqueta en un ZIP los PNG ya renderizados de varias sesiones (grupos)."""
    items = body.get("items", [])
    if not items:
        raise HTTPException(400, "No se proporcionaron grupos")

    batch_id = str(uuid.uuid4())
    batch_dir = SESSIONS_DIR / "_batch" / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    zip_path = batch_dir / "bodegones.zip"

    used: set = set()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for it in items:
            sid = it.get("session_id", "")
            if not re.match(r"^[0-9a-f\-]{36}$", sid):
                continue
            png = SESSIONS_DIR / sid / "output" / "bodegon.png"
            if not png.exists():
                continue
            base = _safe_name(it.get("name") or sid).rsplit(".", 1)[0]
            arc = f"{base}.png"
            n = 1
            while arc in used:
                arc = f"{base}_{n}.png"
                n += 1
            used.add(arc)
            zf.write(str(png), arc)

    if not used:
        shutil.rmtree(batch_dir, ignore_errors=True)
        raise HTTPException(404, "No hay bodegones renderizados para empaquetar")

    return {"batch_id": batch_id, "count": len(used)}


@app.get("/api/batch/download/{batch_id}")
async def batch_download(batch_id: str):
    if not re.match(r"^[0-9a-f\-]{36}$", batch_id):
        raise HTTPException(400, "Batch ID inválido")
    z = SESSIONS_DIR / "_batch" / batch_id / "bodegones.zip"
    if not z.exists():
        raise HTTPException(404, "ZIP no encontrado")
    return FileResponse(str(z), filename="bodegones.zip", media_type="application/zip")


app.mount("/static", StaticFiles(directory="static"), name="static")
