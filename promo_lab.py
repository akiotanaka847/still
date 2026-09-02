"""Router del módulo experimental de Promos: ingesta libre.

Hace lo mismo que promo.py —leer datos y devolver `fields[]` para el motor de
render— pero desde CUALQUIER origen soportado (hoy .xlsx, .json), no solo
Excel. Vive aparte y bajo su propio prefijo para no tocar nada del módulo que
ya funciona.

Reusa los endpoints de plantillas y assets de promo.py: acá solo cambia de
dónde salen los datos, no cómo se dibujan ni con qué plantilla.
"""
import json

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

import promo_kits
import promo_source_excel  # noqa: F401 — registra el lector .xlsx
import promo_source_json   # noqa: F401 — registra el lector .json
from promo_kits import PromoKitError
from promo_fields import extract_fields_from_row, fields_for_table
from promo_source import PromoSourceError, read_table, supported_extensions

router = APIRouter(prefix="/api/promo-lab", tags=["promo-lab"])


async def _load_table(file: UploadFile = None, data: str = None, filename: str = None):
    """Lee la tabla desde un archivo subido o desde JSON en línea.

    El parámetro `data` es lo que habilita que otro sistema mande la
    información sin construir un archivo — el caso que pidió el usuario.
    """
    if file is not None:
        content = await file.read()
        name = filename or file.filename or ""
    elif data:
        content = data.encode("utf-8")
        name = filename or "datos.json"
    else:
        raise HTTPException(400, "Enviá un archivo en 'file' o JSON en 'data'")

    if not content:
        raise HTTPException(400, "El contenido llegó vacío")

    try:
        return read_table(content, name)
    except PromoSourceError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"No se pudo leer el archivo: {e}")


# ─── Kits de marca ───────────────────────────────────────────────────────────
# Assets, colores y tipografía por cliente. Ver promo_kits.py para el porqué de
# agruparlos en un solo concepto en vez de tres administraciones separadas.

def _kit_guard(fn, *a, **kw):
    """Traduce los errores de dominio del kit a respuestas HTTP."""
    try:
        return fn(*a, **kw)
    except PromoKitError as e:
        raise HTTPException(400, str(e))


@router.get("/kits")
async def kits_list():
    return JSONResponse({
        "kits": promo_kits.list_kits(),
        "asset_types": list(promo_kits.ASSET_TYPES),
    })


@router.post("/kits")
async def kits_create(kit_id: str = Form(...), name: str = Form(None)):
    _kit_guard(promo_kits.create_kit, kit_id, name)
    return JSONResponse({"id": kit_id, "kit": promo_kits.read_kit(kit_id)})


@router.delete("/kits/{kit_id}")
async def kits_delete(kit_id: str):
    _kit_guard(promo_kits.delete_kit, kit_id)
    return JSONResponse({"deleted": kit_id})


@router.get("/kits/{kit_id}")
async def kit_detail(kit_id: str):
    kit = _kit_guard(promo_kits.read_kit, kit_id)
    return JSONResponse({
        "id": kit_id,
        "name": kit.get("name"),
        "colors": kit.get("colors", {}),
        "font": kit.get("font", {}),
        "assets": _kit_guard(promo_kits.list_assets, kit_id),
    })


@router.post("/kits/{kit_id}/assets")
async def kit_upload_assets(
    kit_id: str,
    files: list[UploadFile] = File(...),
    type: str = Form(promo_kits.DEFAULT_TYPE),
):
    guardados, omitidos = [], []
    for up in files:
        try:
            guardados.append(
                promo_kits.save_asset(kit_id, up.filename, await up.read(), type)
            )
        except PromoKitError as e:
            omitidos.append({"filename": up.filename or "(sin nombre)", "reason": str(e)})
    return JSONResponse({
        "saved": guardados,
        "skipped": omitidos,
        "assets": promo_kits.list_assets(kit_id),
    })


@router.delete("/kits/{kit_id}/assets/{filename}")
async def kit_delete_asset(kit_id: str, filename: str):
    _kit_guard(promo_kits.delete_asset, kit_id, filename)
    return JSONResponse({"deleted": filename, "assets": promo_kits.list_assets(kit_id)})


@router.patch("/kits/{kit_id}/assets/{filename}")
async def kit_set_asset_type(kit_id: str, filename: str, type: str = Form(...)):
    _kit_guard(promo_kits.set_asset_type, kit_id, filename, type)
    return JSONResponse({"assets": promo_kits.list_assets(kit_id)})


@router.put("/kits/{kit_id}/colors")
async def kit_set_colors(kit_id: str, colors: str = Form(...)):
    try:
        parsed = json.loads(colors)
    except json.JSONDecodeError:
        raise HTTPException(400, "'colors' debe ser un objeto JSON")
    return JSONResponse({"colors": _kit_guard(promo_kits.set_colors, kit_id, parsed)})


@router.post("/kits/{kit_id}/fonts")
async def kit_upload_font(
    kit_id: str,
    file: UploadFile = File(...),
    weight: str = Form(...),
    family: str = Form(None),
):
    _kit_guard(promo_kits.save_font, kit_id, file.filename, await file.read(), weight)
    if family:
        _kit_guard(promo_kits.set_font_family, kit_id, family)
    return JSONResponse({"font": promo_kits.read_kit(kit_id).get("font", {})})


@router.delete("/kits/{kit_id}/fonts/{weight}")
async def kit_delete_font(kit_id: str, weight: str):
    _kit_guard(promo_kits.delete_font, kit_id, weight)
    return JSONResponse({"font": promo_kits.read_kit(kit_id).get("font", {})})


@router.get("/sources")
async def list_sources():
    """Formatos que este módulo sabe leer hoy."""
    return JSONResponse({"extensions": supported_extensions()})


@router.post("/read")
async def read_one(file: UploadFile = File(None), data: str = Form(None)):
    """Primera fila de datos — una sola promo."""
    table = await _load_table(file, data)
    fields = extract_fields_from_row(table["headers"], table["rows"][0])
    return JSONResponse({"source": table["name"], "fields": fields})


@router.post("/read-batch")
async def read_batch(file: UploadFile = File(None), data: str = Form(None)):
    """Todas las filas — una promo por fila."""
    table = await _load_table(file, data)
    resp = {"source": table["name"], "rows": fields_for_table(table)}
    if table.get("info"):
        resp["info"] = table["info"]   # agrupación y assets faltantes del perfil
    return JSONResponse(resp)
