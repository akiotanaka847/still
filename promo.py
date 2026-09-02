"""Módulo de Promos: Excel → plantilla SVG → PNG / SVG / PSD por capas.

Segundo motor de generación de Still, en paralelo al de bodegones. La
diferencia clave con el resto de la app: acá el render ocurre en el NAVEGADOR
(el SVG se arma con <foreignObject> y se rasteriza a canvas), no en el
servidor. Eso es deliberado y viene del diseño original — negrilla, tachado,
superíndice y ajuste de línea por palabra son nativos del navegador, y
reproducirlos server-side con Pillow daría un resultado distinto al preview.

Por eso el servidor acá hace muy poco: leer el Excel, servir la plantilla y
ensamblar el PSD final con las capas que el navegador ya rasterizó. El costo
de CPU por promo generada es prácticamente cero, a diferencia del pipeline de
bodegones (layout.py + renderer.py con Pillow).

La lógica de Excel vive aparte, en promo_excel.py (portada tal cual del repo
original para poder resincronizar).
"""
import base64
import io
import json
import re
import zipfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
from PIL import Image

from promo_excel import extract_fields_from_row, load_workbook_rows
from renderer import compose_psd

router = APIRouter(prefix="/api/promo", tags=["promo"])

PROMO_DIR = Path("static/promo")
TEMPLATES_DIR = PROMO_DIR / "templates"
ASSETS_DIR = PROMO_DIR / "assets"

# Tope de capas por PSD: una plantilla real tiene ~6 campos dinámicos + fondo.
# Es una red de seguridad contra un cliente que mande basura, no un límite
# funcional conocido.
MAX_PSD_LAYERS = 40


def _read_excel_upload(file: UploadFile, content: bytes):
    """Valida y parsea el Excel subido. Comparte el manejo de errores entre
    los dos endpoints de lectura."""
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "El archivo debe ser .xlsx")
    try:
        ws, headers, data_rows, theme_colors = load_workbook_rows(content)
    except Exception as e:
        raise HTTPException(400, f"No se pudo leer el Excel: {e}")
    if not data_rows:
        raise HTTPException(400, "No se encontró ninguna fila de datos visible en el Excel")
    return ws, headers, data_rows, theme_colors


@router.post("/read-excel")
async def read_excel(file: UploadFile = File(...)):
    """Lee la PRIMERA fila de datos visible (una sola promo)."""
    ws, headers, data_rows, theme_colors = _read_excel_upload(file, await file.read())
    fields = extract_fields_from_row(headers, data_rows[0], theme_colors)
    return JSONResponse({"sheet": ws.title, "fields": fields})


@router.post("/read-excel-batch")
async def read_excel_batch(file: UploadFile = File(...)):
    """Lee TODAS las filas visibles — una promo por fila (modo por lotes)."""
    ws, headers, data_rows, theme_colors = _read_excel_upload(file, await file.read())
    rows = [
        {"row_number": i, "fields": extract_fields_from_row(headers, cells, theme_colors)}
        for i, cells in enumerate(data_rows, start=1)
    ]
    return JSONResponse({"sheet": ws.title, "rows": rows})


@router.get("/templates")
async def list_templates():
    """Lista las plantillas disponibles (una carpeta por plantilla)."""
    result = []
    if TEMPLATES_DIR.exists():
        for d in sorted(TEMPLATES_DIR.iterdir()):
            tpl_path = d / "template.json"
            if not tpl_path.exists():
                continue
            try:
                tpl = json.loads(tpl_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            result.append({
                "id": d.name,
                "name": tpl.get("name", d.name),
                "canvas": tpl["canvas"],
                "orientation": tpl.get("orientation", "vertical"),
                "fields": [f.get("name") for f in tpl.get("fields", [])],
            })
    return JSONResponse({"templates": result})


@router.get("/templates/{template_id}")
async def get_template(template_id: str):
    """Devuelve la plantilla con el fondo y las fuentes YA incrustados como
    data URLs. Es intencional: un <canvas> que rasteriza un SVG con recursos
    externos queda "tainted" y el navegador bloquea exportarlo (toBlob falla
    con SecurityError), así que el SVG debe ser 100% autocontenido."""
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", template_id or ""):
        raise HTTPException(400, "Identificador de plantilla inválido")
    tpl_dir = TEMPLATES_DIR / template_id
    tpl_path = tpl_dir / "template.json"
    if not tpl_path.exists():
        raise HTTPException(404, f"Plantilla no encontrada: {template_id}")
    tpl = json.loads(tpl_path.read_text(encoding="utf-8"))

    bg_path = tpl_dir / tpl["background"]
    tpl["background_data_url"] = None
    if bg_path.exists():
        b64 = base64.b64encode(bg_path.read_bytes()).decode("ascii")
        tpl["background_data_url"] = f"data:image/png;base64,{b64}"

    tpl["fonts"] = {
        f.stem: f"data:font/woff2;base64,{base64.b64encode(f.read_bytes()).decode('ascii')}"
        for f in sorted(tpl_dir.glob("*.woff2"))
    }
    return JSONResponse(tpl)


@router.get("/asset")
async def get_asset(filename: str):
    """Sirve un asset de marca (logo, callout) como data URL."""
    safe_name = Path(filename or "").name      # corta cualquier traversal
    if not safe_name:
        raise HTTPException(400, "Nombre de asset inválido")
    asset_path = ASSETS_DIR / safe_name
    if not asset_path.exists():
        raise HTTPException(404, f"Asset no encontrado: {safe_name}")
    ext = asset_path.suffix.lower().lstrip(".")
    mime = {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "svg": "image/svg+xml", "webp": "image/webp",
    }.get(ext)
    if not mime:
        raise HTTPException(400, f"Tipo de asset no soportado: .{ext}")
    b64 = base64.b64encode(asset_path.read_bytes()).decode("ascii")
    return JSONResponse({"filename": safe_name, "mime": mime,
                         "data_url": f"data:{mime};base64,{b64}"})


@router.get("/assets")
async def list_assets():
    """Lista los assets de marca disponibles, para elegirlos desde la UI."""
    if not ASSETS_DIR.exists():
        return JSONResponse({"assets": []})
    exts = {".png", ".jpg", ".jpeg", ".svg", ".webp"}
    names = sorted(f.name for f in ASSETS_DIR.iterdir()
                   if f.is_file() and f.suffix.lower() in exts)
    return JSONResponse({"assets": names})


@router.post("/zip")
async def build_zip(
    files: list[UploadFile] = File(...),
    names: str = Form(...),
):
    """Empaqueta el lote generado en un ZIP.

    Igual que en /psd, los archivos llegan ya generados por el navegador; el
    servidor solo comprime. Se hace del lado del servidor y no con una
    librería JS de ZIP para no sumar una dependencia de terceros al frontend,
    que hoy no tiene ninguna.
    """
    try:
        file_names = json.loads(names)
    except json.JSONDecodeError:
        raise HTTPException(400, "El campo 'names' debe ser una lista JSON")
    if not isinstance(file_names, list) or len(file_names) != len(files):
        raise HTTPException(400, "'names' debe tener un nombre por cada archivo")
    if not files:
        raise HTTPException(400, "No se recibió ningún archivo")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for upload, name in zip(files, file_names):
            raw = await upload.read()
            if not raw:
                continue
            safe = re.sub(r"[^A-Za-z0-9_\-.]", "_", str(name)) or "promo"
            zf.writestr(safe, raw)

    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="promos.zip"'},
    )


@router.post("/psd")
async def build_psd(
    layers: list[UploadFile] = File(...),
    names: str = Form(...),
    filename: str = Form("promo"),
):
    """Ensambla un PSD con una capa por campo de la plantilla.

    Las capas llegan YA rasterizadas por el navegador (un PNG transparente
    por campo, todos del mismo tamaño que el lienzo). Es a propósito: el
    navegador es quien sabe dibujar el texto con negrilla/tachado/superíndice
    por palabra, así que rasterizar ahí garantiza que el PSD coincida
    exactamente con el preview. El servidor solo apila.

    names: JSON con la lista de nombres de capa, en el MISMO orden que
    'layers', de atrás hacia adelante.
    """
    try:
        layer_names = json.loads(names)
    except json.JSONDecodeError:
        raise HTTPException(400, "El campo 'names' debe ser una lista JSON")
    if not isinstance(layer_names, list) or len(layer_names) != len(layers):
        raise HTTPException(400, "'names' debe tener un nombre por cada capa enviada")
    if not layers:
        raise HTTPException(400, "No se recibió ninguna capa")
    if len(layers) > MAX_PSD_LAYERS:
        raise HTTPException(413, f"Demasiadas capas (máximo {MAX_PSD_LAYERS})")

    stack = []
    size = None
    for upload, name in zip(layers, layer_names):
        raw = await upload.read()
        if not raw:
            continue
        try:
            img = Image.open(io.BytesIO(raw)).convert("RGBA")
        except Exception as e:
            raise HTTPException(400, f"Capa '{name}' no es una imagen válida: {e}")
        if size is None:
            size = img.size
        elif img.size != size:
            raise HTTPException(400, "Todas las capas deben tener el mismo tamaño")
        # Recortar cada capa a su contenido real y guardar el offset. El
        # navegador manda cada capa del tamaño COMPLETO del lienzo (con todo
        # transparente salvo su campo), y a resoluciones de plantilla reales
        # (ej. 20226x6122) eso da PSDs de cientos de MB casi todos vacíos.
        # En PSD cada capa tiene sus propios límites, así que recortar es
        # además lo idiomático — no se pierde nada.
        bbox = img.getbbox()
        if bbox:
            left, top = bbox[0], bbox[1]
            img = img.crop(bbox)
        else:
            left = top = 0    # capa vacía: se conserva, para no perder el campo
        stack.append((str(name)[:255] or "Capa", img, left, top))

    if not stack:
        raise HTTPException(400, "Todas las capas llegaron vacías")

    buf = io.BytesIO()
    try:
        compose_psd(stack, size, buf)
    except Exception as e:
        raise HTTPException(500, f"Error armando el PSD: {e}")

    safe = re.sub(r"[^A-Za-z0-9_\-]", "_", filename) or "promo"
    return Response(
        content=buf.getvalue(),
        media_type="image/vnd.adobe.photoshop",
        headers={"Content-Disposition": f'attachment; filename="{safe}.psd"'},
    )
