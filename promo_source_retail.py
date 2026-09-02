"""Perfil de origen: export del sistema de planeación promocional.

Traduce el JSON que exporta el sistema del usuario (archivos tipo
"Consolidado <categoría> <fecha>.json") al contrato neutro, para poder subirlo
TAL CUAL sin convertirlo a mano.

Tres cosas que este formato necesita y que un JSON genérico no resuelve:

1. **Agrupar por promoción.** El export trae una fila por PRODUCTO, no por
   promoción: 118 registros pueden ser 88 promos, porque varios productos
   comparten `PROMOCION_ID` (ej. "2 Caguama Indio Agave por $75" aplica a
   Indio Agave, Pacífico y Victoria). Sin agrupar saldrían piezas duplicadas.

2. **Nombres de campo propios.** `PRECIO` en vez de `PESOS O PRECIO`,
   `PRODUCTO_DESC` en vez de `DESCRIPCION`, etc.

3. **Assets por texto.** El export no trae nombres de archivo sino el texto
   ("NUEVO", "HEINEKEN"); se resuelven contra la biblioteca (ver promo_assets).

Los campos de producción física (POP, ESPACIO, VERSION, IMPRENTA, CANTIDAD_*)
se ignoran a propósito: describen en qué material se imprime la pieza, no qué
promoción es. Acá solo se genera la promo.
"""
import collections

from promo_assets import resolve_asset, _index as _asset_index
from promo_source import make_cell, make_table

# Clave que identifica a este formato dentro del JSON.
SECTION = "Reporte_Promociones"
GROUP_BY = "PROMOCION_ID"

# Columnas canónicas que produce este perfil, en el orden en que se muestran.
HEADERS = [
    "Nombre corto", "ORIENTACION", "CALLOUT", "FRASE APOYO",
    "MULTIPLICADOR", "PESOS O PRECIO", "DESCUENTO", "DESCRIPCION", "BRAND",
]


def looks_like_retail(doc) -> bool:
    """True si el documento es un export de este sistema."""
    if isinstance(doc, list):
        doc = doc[0] if doc and isinstance(doc[0], dict) else {}
    return isinstance(doc, dict) and SECTION in doc


def _records(doc):
    if isinstance(doc, list):
        doc = doc[0] if doc else {}
    recs = doc.get(SECTION) or []
    return [r for r in recs if isinstance(r, dict)]


def _txt(record, key):
    v = record.get(key)
    return "" if v is None else str(v).strip()


def _descripcion(items):
    """Descripción de la pieza: los productos que abarca la promoción.

    Se usa PRODUCTO_DESC y no DESCRIPCION_MECANICA porque esa última es la
    mecánica completa ("2 Caguama Indio Agave de 940 ml por $75") e incluye el
    precio, que la plantilla ya dibuja aparte — quedaría repetido.

    Cuando la promo abarca varios productos se listan separados por " o ",
    que es como se lee la mecánica ("Tecate Light o Tecate Original").
    """
    vistos = []
    for it in items:
        p = _txt(it, "PRODUCTO_DESC")
        if p and p not in vistos:
            vistos.append(p)
    return " o ".join(vistos)


def to_table(doc, orientation="V"):
    """Export -> Table neutra, una fila por PROMOCION_ID.

    Devuelve (table, info) donde `info` reporta qué se agrupó y qué assets
    faltan en la biblioteca — eso último es accionable: es la lista exacta de
    imágenes que hay que subir para que las promos salgan completas.
    """
    recs = _records(doc)
    grupos = collections.OrderedDict()
    for rec in recs:
        grupos.setdefault(_txt(rec, GROUP_BY) or f"_sin_id_{len(grupos)}", []).append(rec)

    idx = _asset_index()
    faltantes = []

    def asset(text):
        """Texto -> archivo del asset, anotando el faltante si no existe."""
        if not text:
            return ""
        found = resolve_asset(text, idx)
        if found:
            return found
        if text not in faltantes:
            faltantes.append(text)
        return ""

    rows = []
    for pid, items in grupos.items():
        p = items[0]

        # TEXTO APOYO alimenta el callout O la frase de apoyo, nunca las dos:
        # si existe la placa gráfica para ese texto ("NUEVO" -> CA_NUEVO.svg),
        # se usa la placa y el texto NO se repite abajo. Si no existe, el texto
        # se muestra como frase de apoyo, que es la degradación natural.
        texto_apoyo = _txt(p, "TEXTO APOYO")
        callout = resolve_asset(texto_apoyo, idx) if texto_apoyo else None
        frase = "" if callout else texto_apoyo

        rows.append([
            make_cell(value=pid),
            make_cell(value=orientation),
            make_cell(value=callout or ""),                      # CALLOUT
            make_cell(value=frase),                              # FRASE APOYO
            make_cell(value=_txt(p, "MULTIPLICADOR")),
            make_cell(value=_txt(p, "PRECIO")),
            make_cell(value=_txt(p, "DESCUENTO")),
            make_cell(value=_descripcion(items)),
            make_cell(value=asset(_txt(p, "PROVEEDOR"))),        # BRAND
        ])

    info = {
        "registros": len(recs),
        "promociones": len(rows),
        "assets_faltantes": sorted(faltantes),
    }
    return make_table("Reporte de promociones", HEADERS, rows), info
