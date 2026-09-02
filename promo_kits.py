"""Kits de marca: assets, colores y tipografía por cliente.

Las tres cosas que varían entre clientes —las imágenes, los colores y las
fuentes— varían por el MISMO eje, así que viven juntas en un "kit" en vez de
en tres administraciones separadas. Elegir el kit es elegir la identidad
visual completa con la que se genera.

    static/promo/kits/<id>/
        kit.json      nombre, colores, tipografía
        assets/       plecas, callouts y marcas
        fonts/        woff2 del cliente

Los kits son COMPARTIDOS entre los usuarios de Still (decisión del usuario):
el equipo trabaja sobre los mismos clientes, así que duplicar bibliotecas por
persona solo generaría trabajo repetido.

Sobre el TIPO de asset (pleca / callout / marca): no es solo orden. La
búsqueda es por texto, así que sin tipo una marca llamada "NUEVO" y un callout
"NUEVO" colisionan y no hay forma de desempatar. Con tipo, cada campo busca
donde corresponde — PROVEEDOR entre marcas, TEXTO APOYO entre callouts.
"""
import json
import re
import shutil
from pathlib import Path

from promo_excel import normalize

PROMO_DIR = Path("static/promo")
KITS_DIR = PROMO_DIR / "kits"
DEFAULT_KIT = "base"

ASSET_EXTS = (".svg", ".png", ".jpg", ".jpeg", ".webp")
FONT_EXTS = (".woff2",)

# Tipos de asset. "callout" y "pleca" son placas de texto; "marca" es el logo.
ASSET_TYPES = ("callout", "pleca", "marca")
DEFAULT_TYPE = "callout"

# Imágenes que NO son biblioteca del usuario: son los banners que dibujan por
# su cuenta las variantes "doble descripción" (LLÉVATE) y "segundo por"
# (Y LLÉVATE). No se nombran desde los datos, los pone el layout, así que
# borrarlas rompe esas dos variantes.
# El nombre de archivo se puede cambiar sin tocar código, declarándolo en la
# plantilla o en el kit (ver SYSTEM_ASSET_DEFAULTS en static/js/promo.js); esta
# lista es solo para no mezclarlas con las del cliente ni borrarlas sin querer.
SYSTEM_ASSETS = frozenset({
    "CAFIJO_LLEVATE.svg",
    "CAFIJO_Y_LLEVATE.svg",
})

# Prefijos con los que la biblioteca nombra las placas. Se usan para adivinar
# el tipo al migrar lo que ya existía, y para que el texto suelto ("NUEVO")
# encuentre "CA_NUEVO.svg".
_PREFIXES = ("CA", "CAFIJO")

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,48}$")


class PromoKitError(ValueError):
    """Kit o asset inválido. La capa web lo traduce a un 400/404."""


# ─── Utilidades ──────────────────────────────────────────────────────────────
def valid_kit_id(kit_id) -> bool:
    return bool(_ID_RE.match(str(kit_id or "")))


def _kit_dir(kit_id) -> Path:
    """Carpeta del kit, validando el id ANTES de construir la ruta.

    La validación es obligatoria y no defensiva de más: sobre esta ruta se
    borran archivos, y un id sin validar permitiría salirse de la carpeta.
    """
    if not valid_kit_id(kit_id):
        raise PromoKitError(f"Identificador de kit inválido: {kit_id!r}")
    return KITS_DIR / str(kit_id)


def _safe_filename(name) -> str:
    return re.sub(r"[^A-Za-z0-9_\-.]", "_", Path(str(name or "")).name)


def _default_kit_json(name="Base"):
    return {
        "name": name,
        # Colores del kit: pisan a los de la plantilla, y a su vez el dato
        # puede pisarlos (la cadena queda dato -> kit -> plantilla).
        # Vacío = usar los de la plantilla, que es el comportamiento de hoy.
        "colors": {},
        # Vacío = usar la tipografía que trae la plantilla.
        "font": {},
        # Banners fijos que dibujan ciertas variantes. Vacío = los que trae la
        # plantilla o, si tampoco los declara, los de siempre. Permite que un
        # cliente cambie su LLÉVATE sin tocar código ni la plantilla.
        # Claves: fallback, banner_llevate, banner_y_llevate.
        "system_assets": {},
        "assets": {},   # {archivo: {"type": ...}}
    }


# ─── Lectura ─────────────────────────────────────────────────────────────────
def list_kits():
    out = []
    if not KITS_DIR.exists():
        return out
    for d in sorted(KITS_DIR.iterdir()):
        if not d.is_dir() or not valid_kit_id(d.name):
            continue
        try:
            data = read_kit(d.name)
        except PromoKitError:
            continue
        out.append({
            "id": d.name,
            "name": data.get("name") or d.name,
            "assets": len(data.get("assets", {})),
            "colors": data.get("colors", {}),
            "has_font": bool(data.get("font", {}).get("weights")),
        })
    return out


def read_kit(kit_id) -> dict:
    path = _kit_dir(kit_id) / "kit.json"
    if not path.exists():
        raise PromoKitError(f"Kit no encontrado: {kit_id}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise PromoKitError(f"No se pudo leer el kit {kit_id}: {e}")
    # Completar claves faltantes para que quien lea no tenga que defenderse.
    base = _default_kit_json(data.get("name") or kit_id)
    base.update({k: v for k, v in data.items() if v is not None})
    base.setdefault("assets", {})
    return base


def write_kit(kit_id, data) -> None:
    d = _kit_dir(kit_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "kit.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def assets_dir(kit_id) -> Path:
    return _kit_dir(kit_id) / "assets"


def fonts_dir(kit_id) -> Path:
    return _kit_dir(kit_id) / "fonts"


def list_assets(kit_id):
    """[{file, type}] de un kit, ordenados por tipo y nombre."""
    kit = read_kit(kit_id)
    meta = kit.get("assets", {})
    d = assets_dir(kit_id)
    out = []
    if d.exists():
        for f in sorted(d.iterdir()):
            if f.is_file() and f.suffix.lower() in ASSET_EXTS:
                out.append({
                    "file": f.name,
                    "type": (meta.get(f.name) or {}).get("type") or guess_type(f.name),
                    "system": f.name in SYSTEM_ASSETS,
                })
    # Las del sistema al final: la biblioteca del usuario es lo que importa.
    return sorted(out, key=lambda a: (a["system"], a["type"], a["file"].lower()))


def guess_type(filename) -> str:
    """Tipo probable a partir del nombre, para lo ya existente.

    La convención de la biblioteca es CA_/CAFIJO_ para las placas de texto; el
    resto son logos de marca. Es solo un valor inicial: el usuario lo corrige
    desde la pantalla.
    """
    base = normalize(Path(str(filename)).stem)
    if base.startswith("CAFIJO"):
        return "pleca"
    if base.startswith("CA"):
        return "callout"
    return "marca"


# ─── Resolución por texto, ahora con tipo ────────────────────────────────────
def asset_index(kit_id, tipo=None):
    """{clave normalizada: archivo}, opcionalmente filtrado por tipo.

    Se registra el nombre completo y también la variante sin los prefijos
    CA_/CAFIJO_, para que el texto suelto del origen ("NUEVO") encuentre
    "CA_NUEVO.svg".
    """
    idx = {}
    for a in list_assets(kit_id):
        if tipo and a["type"] != tipo:
            continue
        base = normalize(Path(a["file"]).stem)
        idx.setdefault(base, a["file"])
        for pref in _PREFIXES:
            if base.startswith(pref) and len(base) > len(pref):
                idx.setdefault(base[len(pref):], a["file"])
    return idx


def resolve(text, index):
    if not text:
        return None
    return index.get(normalize(str(text))) or None


# ─── Escritura ───────────────────────────────────────────────────────────────
def save_asset(kit_id, filename, content, tipo=None) -> str:
    ext = Path(_safe_filename(filename)).suffix.lower()
    if ext not in ASSET_EXTS:
        raise PromoKitError(f"Formato de imagen no soportado: {ext or 'sin extensión'}")
    if not content:
        raise PromoKitError("El archivo llegó vacío")

    safe = _safe_filename(filename)
    d = assets_dir(kit_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / safe).write_bytes(content)

    kit = read_kit(kit_id)
    kit.setdefault("assets", {})[safe] = {
        "type": tipo if tipo in ASSET_TYPES else guess_type(safe)
    }
    write_kit(kit_id, kit)
    return safe


def delete_asset(kit_id, filename) -> None:
    """Borra un asset del kit.

    El nombre se sanea y se resuelve DENTRO de la carpeta del kit antes de
    tocar nada — es un borrado real y no puede depender de que quien llame
    haya validado la entrada.
    """
    safe = _safe_filename(filename)
    if not safe:
        raise PromoKitError("Nombre de archivo inválido")
    if safe in SYSTEM_ASSETS:
        raise PromoKitError(
            f"{safe} es del sistema: el motor la usa por nombre fijo y borrarla "
            f"rompería variantes de promoción. No se puede eliminar."
        )
    d = assets_dir(kit_id)
    target = d / safe
    if not target.exists() or not target.is_file():
        raise PromoKitError(f"No existe el asset {safe} en el kit {kit_id}")
    # Cinturón y tirantes: confirmar que el archivo resuelto sigue estando
    # dentro de la carpeta del kit y no se escapó por la ruta.
    if d.resolve() not in target.resolve().parents:
        raise PromoKitError("Ruta de asset fuera del kit")
    target.unlink()

    kit = read_kit(kit_id)
    kit.get("assets", {}).pop(safe, None)
    write_kit(kit_id, kit)


def set_asset_type(kit_id, filename, tipo) -> None:
    if tipo not in ASSET_TYPES:
        raise PromoKitError(f"Tipo inválido: {tipo}. Válidos: {', '.join(ASSET_TYPES)}")
    safe = _safe_filename(filename)
    if not (assets_dir(kit_id) / safe).exists():
        raise PromoKitError(f"No existe el asset {safe} en el kit {kit_id}")
    kit = read_kit(kit_id)
    kit.setdefault("assets", {})[safe] = {"type": tipo}
    write_kit(kit_id, kit)


def set_colors(kit_id, colors) -> dict:
    kit = read_kit(kit_id)
    limpio = {}
    for k, v in (colors or {}).items():
        v = str(v or "").strip()
        if not v:
            continue    # vacío = volver al color de la plantilla
        if not re.match(r"^(rgb\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*\)|#[0-9A-Fa-f]{3,8})$", v):
            raise PromoKitError(f"Color inválido para {k!r}: {v!r}")
        limpio[str(k)] = v
    kit["colors"] = limpio
    write_kit(kit_id, kit)
    return limpio


def save_font(kit_id, filename, content, weight) -> str:
    ext = Path(_safe_filename(filename)).suffix.lower()
    if ext not in FONT_EXTS:
        raise PromoKitError("La fuente debe ser .woff2")
    if not content:
        raise PromoKitError("El archivo llegó vacío")
    try:
        w = int(weight)
    except (TypeError, ValueError):
        raise PromoKitError(f"Peso de fuente inválido: {weight!r}")
    if not (100 <= w <= 900):
        raise PromoKitError("El peso debe estar entre 100 y 900")

    safe = _safe_filename(filename)
    d = fonts_dir(kit_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / safe).write_bytes(content)

    kit = read_kit(kit_id)
    font = kit.setdefault("font", {})
    font.setdefault("family", "Work Sans")
    font.setdefault("weights", {})[str(w)] = safe
    write_kit(kit_id, kit)
    return safe


def delete_font(kit_id, weight) -> None:
    kit = read_kit(kit_id)
    font = kit.get("font", {})
    fname = (font.get("weights") or {}).pop(str(weight), None)
    if not fname:
        raise PromoKitError(f"El kit {kit_id} no tiene fuente para el peso {weight}")
    f = fonts_dir(kit_id) / _safe_filename(fname)
    if f.exists() and f.is_file():
        f.unlink()
    write_kit(kit_id, kit)


def set_font_family(kit_id, family) -> None:
    kit = read_kit(kit_id)
    kit.setdefault("font", {})["family"] = str(family or "").strip() or "Work Sans"
    write_kit(kit_id, kit)


def create_kit(kit_id, name=None) -> dict:
    if not valid_kit_id(kit_id):
        raise PromoKitError(
            "El identificador solo admite minúsculas, números, guion y guion bajo"
        )
    if (_kit_dir(kit_id) / "kit.json").exists():
        raise PromoKitError(f"Ya existe un kit con el identificador {kit_id!r}")
    data = _default_kit_json(name or kit_id)
    write_kit(kit_id, data)
    assets_dir(kit_id).mkdir(parents=True, exist_ok=True)
    fonts_dir(kit_id).mkdir(parents=True, exist_ok=True)
    return data


def delete_kit(kit_id) -> None:
    if kit_id == DEFAULT_KIT:
        raise PromoKitError("El kit base no se puede borrar")
    d = _kit_dir(kit_id)          # valida el id antes de construir la ruta
    if not d.exists() or not d.is_dir():
        raise PromoKitError(f"Kit no encontrado: {kit_id}")
    if KITS_DIR.resolve() not in d.resolve().parents:
        raise PromoKitError("Ruta de kit fuera de la carpeta de kits")
    shutil.rmtree(d)


# ─── Migración de la biblioteca plana que existía antes ──────────────────────
def ensure_base_kit(legacy_assets=PROMO_DIR / "assets") -> bool:
    """Crea el kit base, migrando la biblioteca plana si todavía existiera.

    Devuelve True si migró algo. Es idempotente: si el kit base ya existe no
    toca nada, así que se puede llamar en cada arranque sin riesgo.

    La carpeta plana original ya no está en el repo (la biblioteca vive en el
    kit), pero el parámetro se conserva por si una instalación vieja la tiene:
    si no existe, simplemente se crea el kit vacío.
    """
    base = _kit_dir(DEFAULT_KIT)
    if (base / "kit.json").exists():
        return False

    create_kit(DEFAULT_KIT, "Base")
    kit = read_kit(DEFAULT_KIT)
    migrados = 0
    if legacy_assets.exists():
        dest = assets_dir(DEFAULT_KIT)
        for f in sorted(legacy_assets.iterdir()):
            if f.is_file() and f.suffix.lower() in ASSET_EXTS:
                shutil.copy2(f, dest / f.name)
                kit["assets"][f.name] = {"type": guess_type(f.name)}
                migrados += 1
    write_kit(DEFAULT_KIT, kit)
    return migrados > 0
