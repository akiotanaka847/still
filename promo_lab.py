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

import promo_source_excel  # noqa: F401 — registra el lector .xlsx
import promo_source_json   # noqa: F401 — registra el lector .json
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
    return JSONResponse({"source": table["name"], "rows": fields_for_table(table)})
