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
   ("NUEVO", "HEINEKEN"); se resuelven contra la biblioteca del kit (ver promo_kits).

Los campos de producción física (POP, ESPACIO, VERSION, IMPRENTA, CANTIDAD_*)
se ignoran a propósito: describen en qué material se imprime la pieza, no qué
promoción es. Acá solo se genera la promo.
"""
import collections
import re

import promo_kits
from promo_source import make_cell, make_table

# Clave que identifica a este formato dentro del JSON.
SECTION = "Reporte_Promociones"
GROUP_BY = "PROMOCION_ID"

# Columnas canónicas que produce este perfil, en el orden en que se muestran.
HEADERS = [
    "Nombre corto", "ORIENTACION", "CALLOUT", "FRASE APOYO",
    "MULTIPLICADOR", "PESOS O PRECIO", "DESCUENTO", "DESCRIPCION", "BRAND",
    "PLECA",
    # Ninguna de las dos se dibuja. CATEGORIA alimenta la detección de alcohol
    # (la guía prohíbe "Compra/Llévate/Gratis" en esas promos) y TIPO_MECANICA
    # trae la familia que declara el reporte.
    "CATEGORIA",
    "TIPO_MECANICA",
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


# Cantidad + unidad de medida dentro de la descripción: "330 ml", "1.2 lt",
# "940 ml", "1,5 L". Se resalta el conjunto (número Y unidad), no solo la
# unidad, porque lo que el cliente compara de un vistazo es el contenido.
# El separador puede ser espacio o nada ("330ml"), y el decimal punto o coma.
# "pack" queda FUERA a propósito: es un formato de venta, no una medida, y
# "18 Pack Lata Tecate" terminaba resaltando "18 Pack" en vez del contenido.
_UNIDAD_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(ml|mls|l|lt|lts|litro|litros|mililitro|mililitros|"
    r"g|gr|grs|kg|kgs|gramo|gramos|cc|oz)\b",
    re.IGNORECASE,
)


def resaltar_unidades(texto):
    """Envuelve cantidad+unidad en marcas de negrilla (`*...*`).

    Se devuelve texto con marcas y no runs ya armados a propósito: así pasa por
    el mismo `parse_markup_runs` que cualquier otro origen, y no hay una
    segunda forma de producir formato que pueda desincronizarse.

    Si el texto ya trae marcas de negrilla puestas a mano, no se toca — quien
    escribió el dato decidió el énfasis y no hay que pisarlo.
    """
    if not texto or "*" in texto:
        return texto
    return _UNIDAD_RE.sub(lambda m: f"*{m.group(1)} {m.group(2)}*", texto)


def _descripcion(items):
    """Descripción de la pieza: los productos que abarca la promoción.

    Se usa PRODUCTO_DESC y no DESCRIPCION_MECANICA porque esa última es la
    mecánica completa ("2 Caguama Indio Agave de 940 ml por $75") e incluye el
    precio, que la plantilla ya dibuja aparte — quedaría repetido.

    Cuando la promo abarca varios productos se listan separados por " o ",
    que es como se lee la mecánica ("Tecate Light o Tecate Original").

    La cantidad y la unidad ("330 ml", "1.2 lt") salen en negrilla: es el dato
    que el cliente compara de un vistazo frente a otra promoción.
    """
    vistos = []
    for it in items:
        p = _txt(it, "PRODUCTO_DESC")
        if p and p not in vistos:
            vistos.append(p)
    return resaltar_unidades(" o ".join(vistos))


def to_table(doc, orientation="V", kit=promo_kits.DEFAULT_KIT):
    """Export -> Table neutra, una fila por PROMOCION_ID.

    Devuelve (table, info) donde `info` reporta qué se agrupó y qué assets
    faltan en la biblioteca — eso último es accionable: es la lista exacta de
    imágenes que hay que subir para que las promos salgan completas.
    """
    recs = _records(doc)
    grupos = collections.OrderedDict()
    for rec in recs:
        grupos.setdefault(_txt(rec, GROUP_BY) or f"_sin_id_{len(grupos)}", []).append(rec)

    # Índices SEPARADOS por tipo: sin esto, una marca y un callout con el mismo
    # texto ("NUEVO") colisionan y el resultado depende del orden alfabético.
    idx_callout = promo_kits.asset_index(kit, "callout")
    idx_pleca = promo_kits.asset_index(kit, "pleca")
    idx_marca = promo_kits.asset_index(kit, "marca")
    faltantes = []

    def asset(text, index, que):
        """Texto -> archivo del asset, anotando el faltante si no existe."""
        if not text:
            return ""
        found = promo_kits.resolve(text, index)
        if found:
            return found
        etiqueta = f"{text} ({que})"
        if etiqueta not in faltantes:
            faltantes.append(etiqueta)
        return ""

    rows = []
    for pid, items in grupos.items():
        p = items[0]

        # TEXTO APOYO alimenta el callout O la frase de apoyo, nunca las dos:
        # si existe la placa gráfica para ese texto ("NUEVO" -> CA_NUEVO.svg),
        # se usa la placa y el texto NO se repite abajo. Si no existe, el texto
        # se muestra como frase de apoyo, que es la degradación natural.
        # La placa puede estar catalogada como callout o como pleca: son dos
        # tipos distintos de placa de texto y el origen no distingue cuál es.
        texto_apoyo = _txt(p, "TEXTO APOYO")
        callout = None
        if texto_apoyo:
            callout = (promo_kits.resolve(texto_apoyo, idx_callout)
                       or promo_kits.resolve(texto_apoyo, idx_pleca))
            if not callout:
                asset(texto_apoyo, idx_callout, "placa")   # solo para anotarlo
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
            make_cell(value=asset(_txt(p, "PROVEEDOR"), idx_marca, "marca")),  # BRAND
            # La columna PLECA trae un TEXTO, no un nombre de archivo (visto en
            # los datos reales: "¡Combínalos como quieras! | Ahorra desde",
            # "Edición Especial"), igual que TEXTO APOYO trae "NUEVO". Se busca
            # entre las imágenes catalogadas como pleca. Si no hay imagen para
            # ese texto, no se dibuja nada: la pleca es una placa diseñada, no
            # un texto suelto, así que no tiene degradación a texto.
            make_cell(value=asset(_txt(p, "PLECA"), idx_pleca, "pleca")),      # PLECA
            make_cell(value=_txt(p, "CATEGORIA")),                             # CATEGORIA
            make_cell(value=_txt(p, "TIPO_MECANICA")),                         # TIPO_MECANICA
        ])

    info = {
        "registros": len(recs),
        "promociones": len(rows),
        "assets_faltantes": sorted(faltantes),
    }
    return make_table("Reporte de promociones", HEADERS, rows), info
