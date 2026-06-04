"""
PSD Smart Object Replacer — FastAPI backend
Módulo 1: Reemplaza SmartObjectLayers en archivos PSD/PSB.
Módulo 2: Generador de bodegones con layout automático + propuesta IA.
"""
import hashlib
import hmac
import json
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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from layout import compute_layout
from renderer import get_image_dimensions, render_png

# ─── Importar psapi ──────────────────────────────────────────────────────────
psapi = None
try:
    import psapi  # noqa: F811
except ImportError:
    try:
        import photoshopapi as psapi  # noqa: F811
    except ImportError:
        pass

PSAPI_AVAILABLE = psapi is not None

# ─── Directorios ─────────────────────────────────────────────────────────────
SESSIONS_DIR = Path("sessions")
STATIC_DIR = Path("static")
SESSIONS_DIR.mkdir(exist_ok=True)
STATIC_DIR.mkdir(exist_ok=True)


# ─── Autenticación ────────────────────────────────────────────────────────────
USERS_FILE = Path("users.json")
SECRET_FILE = Path(".secret")

# Prefijos de rutas que requieren sesión iniciada
PROTECTED_PREFIXES = ("/api/bodegon", "/api/batch", "/api/analyze", "/api/process", "/api/download")


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


def _open_psd(path: str):
    """Abre un PSD/PSB detectando automáticamente la profundidad de bits."""
    if hasattr(psapi, "LayeredFile") and hasattr(psapi.LayeredFile, "read"):
        return psapi.LayeredFile.read(path)
    for cls_name in ("LayeredFile_8bit", "LayeredFile_16bit", "LayeredFile_32bit"):
        cls = getattr(psapi, cls_name, None)
        if cls:
            try:
                return cls.read(path)
            except Exception:
                continue
    raise ValueError("No se pudo abrir el archivo PSD")


def _collect_smart_objects(layers, parent_path: str = "") -> list:
    """Recorre el árbol de capas y retorna todos los SmartObjectLayer con su ruta."""
    result = []
    for layer in layers:
        name = layer.name
        path = f"{parent_path}/{name}" if parent_path else name
        type_name = type(layer).__name__

        if "SmartObject" in type_name:
            result.append({"name": name, "path": path})

        if "Group" in type_name:
            try:
                result.extend(_collect_smart_objects(layer.layers, path))
            except Exception:
                pass
    return result


def _apply_to_layer(
    layers,
    target_path: str,
    image_path: Optional[str],
    new_name: Optional[str],
    current_path: str = "",
) -> bool:
    """Busca el SmartObjectLayer por ruta y aplica reemplazo de imagen y/o renombrado."""
    for layer in layers:
        name = layer.name
        path = f"{current_path}/{name}" if current_path else name
        type_name = type(layer).__name__

        if "SmartObject" in type_name and path == target_path:
            if image_path:
                layer.replace(image_path, link_externally=False)
            if new_name and new_name.strip() and new_name.strip() != name:
                layer.name = new_name.strip()
            return True

        if "Group" in type_name:
            try:
                if _apply_to_layer(layer.layers, target_path, image_path, new_name, path):
                    return True
            except Exception:
                pass
    return False


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
app = FastAPI(title="PSD Smart Object Replacer", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


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
    https_only=False,
    max_age=60 * 60 * 24 * 7,   # 7 días
)


# ─── Rutas ────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def frontend() -> HTMLResponse:
    html_path = STATIC_DIR / "index.html"
    if not html_path.exists():
        raise HTTPException(404, "Frontend no encontrado")
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/api/status")
async def status():
    return {"psapi_available": PSAPI_AVAILABLE}


# ─── Auth ─────────────────────────────────────────────────────────────────────
@app.post("/api/auth/register")
async def auth_register(request: Request, body: dict):
    u = (body.get("username") or "").strip().lower()
    p = body.get("password") or ""
    if len(u) < 3 or len(p) < 4:
        raise HTTPException(400, "Usuario (mín 3) y contraseña (mín 4 caracteres) requeridos")
    if not re.match(r"^[a-z0-9_.\-]+$", u):
        raise HTTPException(400, "Usuario inválido (usa letras, números, . _ -)")
    users = _load_users()
    if u in users:
        raise HTTPException(409, "Ese usuario ya existe")
    users[u] = {"password": _hash_pw(p), "created": time.time()}
    _save_users(users)
    request.session["user"] = u          # inicia sesión automáticamente
    return {"username": u}


@app.post("/api/auth/login")
async def auth_login(request: Request, body: dict):
    u = (body.get("username") or "").strip().lower()
    p = body.get("password") or ""
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


@app.post("/api/analyze")
async def analyze(file: UploadFile = File(...)):
    if not PSAPI_AVAILABLE:
        raise HTTPException(
            503,
            "PhotoshopAPI no está instalado. Ejecuta en la terminal: pip install PhotoshopAPI",
        )

    ext = Path(file.filename or "").suffix.lower()
    if ext not in (".psd", ".psb"):
        raise HTTPException(400, "Solo se aceptan archivos .psd y .psb")

    session_id = str(uuid.uuid4())
    session_dir = SESSIONS_DIR / session_id
    (session_dir / "original").mkdir(parents=True)

    safe = _safe_name(file.filename or "file.psd")
    psd_path = session_dir / "original" / safe
    psd_path.write_bytes(await file.read())

    try:
        doc = _open_psd(str(psd_path))
        smart_objects = _collect_smart_objects(doc.layers)

        info = {
            "session_id": session_id,
            "filename": safe,
            "psd_path": str(psd_path),
            "width": int(doc.width),
            "height": int(doc.height),
            "smart_objects": smart_objects,
        }
        (session_dir / "session.json").write_text(
            json.dumps(info, indent=2), encoding="utf-8"
        )
        return info

    except Exception as e:
        shutil.rmtree(session_dir, ignore_errors=True)
        raise HTTPException(500, f"Error leyendo PSD: {e}")


@app.post("/api/process")
async def process(
    session_id: str = Form(...),
    replacements: str = Form(...),
    files: List[UploadFile] = File(default=[]),
):
    if not PSAPI_AVAILABLE:
        raise HTTPException(503, "PhotoshopAPI no está instalado")

    if not re.match(r"^[0-9a-f\-]{36}$", session_id):
        raise HTTPException(400, "Session ID inválido")

    session_dir = SESSIONS_DIR / session_id
    session_file = session_dir / "session.json"
    if not session_file.exists():
        raise HTTPException(404, "Sesión no encontrada o expirada")

    info = json.loads(session_file.read_text(encoding="utf-8"))
    replacements_list: list = json.loads(replacements)

    images_dir = session_dir / "images"
    images_dir.mkdir(exist_ok=True)

    # Guardar imágenes subidas con el file_key como nombre
    file_map: dict[str, str] = {}
    for f in files:
        raw_key = f.filename or "img"
        safe_key = _safe_name(raw_key)
        dest = images_dir / safe_key
        dest.write_bytes(await f.read())
        file_map[raw_key] = str(dest)

    doc = _open_psd(info["psd_path"])
    applied: list[str] = []
    errors: list[str] = []

    for r in replacements_list:
        path: str = r.get("path", "")
        new_name: str = r.get("new_name", "").strip()
        file_key: str = r.get("file_key", "")

        if not file_key and not new_name:
            continue

        image_path = file_map.get(file_key) if file_key else None

        try:
            ok = _apply_to_layer(doc.layers, path, image_path, new_name or None)
            if ok:
                applied.append(path)
            else:
                errors.append(f"No encontrado: '{path}'")
        except Exception as e:
            errors.append(f"Error en '{path}': {e}")

    output_dir = session_dir / "output"
    output_dir.mkdir(exist_ok=True)
    out_name = f"modified_{info['filename']}"
    out_path = output_dir / out_name

    try:
        doc.write(str(out_path))
    except Exception as e:
        raise HTTPException(500, f"Error guardando PSD: {e}")

    return {
        "session_id": session_id,
        "filename": out_name,
        "applied": applied,
        "errors": errors,
    }


@app.get("/api/download/{session_id}")
async def download(session_id: str):
    if not re.match(r"^[0-9a-f\-]{36}$", session_id):
        raise HTTPException(400, "Session ID inválido")
    output_dir = SESSIONS_DIR / session_id / "output"
    files = sorted(output_dir.glob("*.ps*")) if output_dir.exists() else []
    if not files:
        raise HTTPException(404, "Archivo de salida no encontrado")
    f = files[0]
    return FileResponse(str(f), filename=f.name, media_type="application/octet-stream")


# ══════════════════════════════════════════════════════════════════════════════
# MÓDULO 2: BODEGÓN GENERATOR
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
    session_id = str(uuid.uuid4())
    session_dir = SESSIONS_DIR / session_id
    products_dir = session_dir / "products"
    products_dir.mkdir(parents=True)

    images = []
    for i, f in enumerate(products):
        ext = Path(f.filename or "").suffix.lower()
        if ext not in VALID_IMAGE_EXTS:
            continue

        safe = _safe_name(f.filename or f"product_{i}{ext}")
        dest = products_dir / safe
        dest.write_bytes(await f.read())

        try:
            w, h = get_image_dimensions(str(dest))
        except Exception:
            dest.unlink(missing_ok=True)
            continue

        images.append({
            "name": Path(safe).stem.upper().replace(" ", "_"),
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
        if ext in VALID_IMAGE_EXTS:
            bg_safe = _safe_name(background.filename)
            bg_dest = session_dir / bg_safe
            bg_dest.write_bytes(await background.read())
            bg_path = str(bg_dest)

    info = {
        "session_id": session_id,
        "images": images,
        "background_path": bg_path,
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
