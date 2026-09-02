"""Resolución de assets por TEXTO, no por nombre de archivo.

El módulo de Promos original espera que las columnas CALLOUT y BRAND traigan el
nombre exacto del archivo ("CA_NUEVO.svg"). Eso funciona cuando quien arma la
planilla conoce la biblioteca, pero no cuando los datos vienen de otro sistema:
ahí lo que llega es el TEXTO ("NUEVO", "Combínalos como quieras", "HEINEKEN").

Este módulo traduce ese texto al archivo que le corresponde, si existe. La
convención de nombres de la biblioteca ya lo permitía: los callouts se llaman
`CA_<TEXTO>.svg` / `CAFIJO_<TEXTO>.svg` y las marcas `<Marca>.svg`, así que
alcanza con normalizar (quitar tildes, espacios y símbolos) y probar también
sin esos prefijos.

Verificado contra la biblioteca real:
    "NUEVO"                   -> CA_NUEVO.svg
    "Combínalos como quieras" -> CA_COMBINALOS_COMO_QUIERAS.svg
    "Tú eliges"               -> CA_TUELIGES.svg
    "Llévate"                 -> CAFIJO_LLEVATE.svg
    "Coca-Cola"               -> CocaCola.svg
"""
from pathlib import Path

from promo_excel import normalize

ASSETS_DIR = Path("static/promo/assets")
ASSET_EXTS = (".svg", ".png", ".jpg", ".jpeg", ".webp")

# Prefijos con los que la biblioteca nombra los callouts. Se prueban además del
# nombre completo, para que el texto suelto ("NUEVO") encuentre "CA_NUEVO.svg".
_PREFIXES = ("CA", "CAFIJO")


def _index():
    """{clave normalizada: nombre de archivo}. Se relee en cada llamada porque
    la biblioteca cambia cuando el usuario sube un asset desde la pantalla."""
    idx = {}
    if not ASSETS_DIR.exists():
        return idx
    for f in sorted(ASSETS_DIR.iterdir()):
        if not f.is_file() or f.suffix.lower() not in ASSET_EXTS:
            continue
        base = normalize(f.stem)
        # El nombre completo tiene prioridad sobre la variante sin prefijo, por
        # eso se usa setdefault y se registra el completo primero.
        idx.setdefault(base, f.name)
        for pref in _PREFIXES:
            if base.startswith(pref) and len(base) > len(pref):
                idx.setdefault(base[len(pref):], f.name)
    return idx


def resolve_asset(text, index=None):
    """Nombre de archivo del asset que corresponde a `text`, o None.

    Devolver None en vez de un archivo de reemplazo es deliberado: quien llama
    puede así omitir el campo (la promo sale sin ese elemento) y avisar qué
    falta subir, en vez de estampar la imagen de error sin explicación.
    """
    if not text:
        return None
    key = normalize(str(text))
    if not key:
        return None
    return (index if index is not None else _index()).get(key)


def resolve_many(textos):
    """Resuelve varios textos de una sola pasada por la biblioteca.

    Devuelve (encontrados, faltantes): el dict {texto: archivo} y la lista
    ordenada de los textos sin asset, que es lo que hay que subir.
    """
    idx = _index()
    encontrados, faltantes = {}, []
    for t in textos:
        if not t:
            continue
        found = resolve_asset(t, idx)
        if found:
            encontrados[t] = found
        elif t not in faltantes:
            faltantes.append(t)
    return encontrados, sorted(faltantes)
