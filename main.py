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

from fastapi import FastAPI, File, HTTPException, UploadFile, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from dataclasses import asdict

from layout import compute_layout
from renderer import get_image_dimensions, render_png
from normalize import BgMethod, NormalizeStatus, normalize_image


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
SESSIONS_DIR = Path("sessions")
STATIC_DIR = Path("static")
SESSIONS_DIR.mkdir(exist_ok=True)
STATIC_DIR.mkdir(exist_ok=True)


# ─── Autenticación ────────────────────────────────────────────────────────────
USERS_FILE = Path("users.json")
SECRET_FILE = Path(".secret")

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

# Content-Security-Policy (permite lo que la app necesita: Tailwind CDN, Google Fonts,
# imágenes data:/blob: y código inline). 'unsafe-inline' es necesario por el JS/CSS inline.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; "
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
AI_VARIANT_DEFS = [
    ("ai",        "IA · Equilibrado", "Orden y tamaños inteligentes"),
    ("ai_depth",  "IA · Profundidad", "Héroe al frente + solapamiento"),
    ("ai_shadow", "IA · Sombra",      "Anclado con sombra de contacto"),
]
AI_STRATEGY_IDS = {vid for vid, _, _ in AI_VARIANT_DEFS}


@app.post("/api/bodegon/upload")
async def bodegon_upload(
    products: List[UploadFile] = File(...),
    background: Optional[UploadFile] = File(default=None),
):
    """Sube imágenes de productos y (opcionalmente) un fondo. Retorna dimensiones."""
    if len(products) > MAX_FILES:
        raise HTTPException(413, f"Máximo {MAX_FILES} imágenes por grupo")

    session_id = str(uuid.uuid4())
    session_dir = SESSIONS_DIR / session_id
    products_dir = session_dir / "products"
    products_dir.mkdir(parents=True)

    # Método de remoción de fondo configurable (rembg CPU en F1, BiRefNet GPU en F2).
    try:
        bg_method = BgMethod(os.environ.get("BG_METHOD", "rembg"))
    except ValueError:
        bg_method = BgMethod.REMBG

    images = []
    failures = []   # SKUs sin packshot usable — se reportan, no se compositan
    degraded = []   # SKUs con packshot de baja confianza — requieren revisión humana
    for i, f in enumerate(products):
        ext = Path(f.filename or "").suffix.lower()
        if ext not in VALID_IMAGE_EXTS:
            continue

        data = await f.read()
        if len(data) > MAX_FILE_BYTES:
            continue   # ignora archivos demasiado grandes

        safe = _safe_name(f.filename or f"product_{i}{ext}")
        name = Path(safe).stem.upper().replace(" ", "_")
        raw_dest = products_dir / safe
        raw_dest.write_bytes(data)

        # Normalización en ingesta (UNA vez): alpha → (bg removal) → trim → features.
        # El motor consume el PACKSHOT (transparente, bbox ajustado), no el raw.
        pack_dest = products_dir / f"{Path(safe).stem}.packshot.png"
        res = normalize_image(str(raw_dest), str(pack_dest), bg_method=bg_method, name=name)

        if res.status == NormalizeStatus.FAILED:
            # Falla explícita: NUNCA se pasa la imagen cruda (caja blanca) aguas abajo.
            failures.append({"filename": safe, "name": name, "error": res.error})
            raw_dest.unlink(missing_ok=True)
            continue

        feats = asdict(res.features)
        entry = {
            "name": name,
            "filename": safe,
            "filepath": str(pack_dest),          # ← el motor consume el PACKSHOT
            "raw_filepath": str(raw_dest),
            "width": res.features.width,         # dims del bbox ajustado (sin re-trim)
            "height": res.features.height,
            "features": feats,                   # dominant_color, orientation, ratio, hint
            "normalize": {
                "status": res.status.value,
                "method": res.method.value,
                "confidence": res.confidence,
            },
        }
        images.append(entry)
        if res.status == NormalizeStatus.DEGRADED:
            degraded.append({"filename": safe, "name": name,
                             "confidence": res.confidence, "method": res.method.value})

    if not images:
        shutil.rmtree(session_dir, ignore_errors=True)
        detail = "No se encontraron imágenes válidas"
        if failures:
            detail += f" ({len(failures)} fallaron la normalización)"
        raise HTTPException(400, detail)

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
        "failures": failures,
        "degraded": degraded,
    }
    (session_dir / "bodegon.json").write_text(json.dumps(info, indent=2), encoding="utf-8")

    return {
        "session_id": session_id,
        "count": len(images),
        "images": [
            {"name": img["name"], "filename": img["filename"],
             "width": img["width"], "height": img["height"],
             "features": img.get("features"), "normalize": img.get("normalize")}
            for img in images
        ],
        "has_background": bg_path is not None,
        "failures": failures,    # gate de revisión: SKUs sin packshot usable
        "degraded": degraded,    # SKUs de baja confianza para revisión humana
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
    layout = compute_layout(
        images=info["images"],
        sort_strategy=cfg.get("sort_strategy", "auto"),
        rows_count=int(cfg.get("rows_count", 0)),
        base_height=int(cfg.get("base_height", 760)),
        item_gap=int(cfg.get("item_gap", 40)),
        internal_padding=int(cfg.get("internal_padding", 120)),
        max_vertical_boost=float(cfg.get("max_vertical_boost", 1.20)),
        aspect_w=int(cfg.get("aspect_w", 0)),
        aspect_h=int(cfg.get("aspect_h", 0)),
    )

    info["layout"] = layout
    info_file.write_text(json.dumps(info, indent=2), encoding="utf-8")

    return layout


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

    # Re-computar si hay config nueva en el body
    if "config" in body:
        cfg = body["config"]
        # Para las estrategias de IA usamos las imágenes ordenadas + escaladas
        # que persistió /ai-proposal
        if cfg.get("sort_strategy") in AI_STRATEGY_IDS and info.get("ai_images"):
            render_images = info["ai_images"]
        else:
            render_images = info["images"]
        layout = compute_layout(
            images=render_images,
            sort_strategy=cfg.get("sort_strategy", "auto"),
            rows_count=int(cfg.get("rows_count", 0)),
            base_height=int(cfg.get("base_height", 760)),
            item_gap=int(cfg.get("item_gap", 40)),
            internal_padding=int(cfg.get("internal_padding", 120)),
            max_vertical_boost=float(cfg.get("max_vertical_boost", 1.20)),
            aspect_w=int(cfg.get("aspect_w", 0)),
            aspect_h=int(cfg.get("aspect_h", 0)),
        )
    elif "layout" in info:
        layout = info["layout"]
    else:
        raise HTTPException(400, "Ejecuta /compute primero")

    output_dir = session_dir / "output"
    output_dir.mkdir(exist_ok=True)
    out_path = output_dir / "bodegon.png"

    try:
        render_png(layout, str(out_path), background_path=info.get("background_path"))
    except Exception as e:
        raise HTTPException(500, f"Error renderizando PNG: {e}")

    return {"session_id": session_id, "filename": "bodegon.png"}


@app.get("/api/bodegon/download/{session_id}")
async def bodegon_download(session_id: str):
    """Descarga el PNG generado."""
    if not re.match(r"^[0-9a-f\-]{36}$", session_id):
        raise HTTPException(400, "Session ID inválido")
    out = SESSIONS_DIR / session_id / "output" / "bodegon.png"
    if not out.exists():
        raise HTTPException(404, "PNG no encontrado. Ejecuta el render primero.")
    return FileResponse(str(out), filename="bodegon.png", media_type="image/png")


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
    provider = os.environ.get("AI_PROVIDER", "").strip().lower() or None
    if not (groq_key or gemini_key or anthropic_key):
        raise HTTPException(503, "Configura GROQ_API_KEY, GEMINI_API_KEY o ANTHROPIC_API_KEY en .env")

    info = json.loads(info_file.read_text(encoding="utf-8"))

    try:
        from ai_proposal import analyze_products
        suggestion = analyze_products(
            info["images"],
            groq_key=groq_key or None,
            gemini_key=gemini_key or None,
            anthropic_key=anthropic_key or None,
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

    # Persistir el orden + tamaños para que el render del PNG los respete
    info["ai_images"] = ordered
    info_file.write_text(json.dumps(info, indent=2), encoding="utf-8")

    # Varias propuestas IA desde el MISMO análisis (orden + tamaños), distinto layout
    cfg = body.get("config", {})

    def _ai_layout(strategy_id: str):
        return compute_layout(
            images=ordered,
            sort_strategy=strategy_id,
            rows_count=int(cfg.get("rows_count", 0)),
            base_height=int(cfg.get("base_height", 760)),
            item_gap=int(cfg.get("item_gap", 40)),
            internal_padding=int(cfg.get("internal_padding", 120)),
            max_vertical_boost=float(cfg.get("max_vertical_boost", 1.20)),
            aspect_w=int(cfg.get("aspect_w", 0)),
            aspect_h=int(cfg.get("aspect_h", 0)),
        )

    variants = [
        {"id": vid, "label": label, "desc": desc, "layout": _ai_layout(vid)}
        for vid, label, desc in AI_VARIANT_DEFS
    ]

    return {
        "variants": variants,
        "layout": variants[0]["layout"],   # compat
        "suggestion": suggestion,
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
