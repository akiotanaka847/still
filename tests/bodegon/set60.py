"""Banco de productos para probar el techo de escala de Bodegón.

Reúne SKUs únicos de las sesiones reales en disco. El nombre de archivo ES el
SKU (misma convención que fija ROADMAP_PLATAFORMA.md), así que deduplicar por
nombre da productos distintos y no la misma foto repetida — que es lo que
invalidaría cualquier medición de "¿aguanta 60?".
"""
import glob
import os
import re
from pathlib import Path

from PIL import Image

# Anclado a la raíz del repo, no al cwd: estos scripts se corren tanto desde
# la raíz como desde tests/bodegon/ y el glob relativo devolvía 0 en el segundo caso.
RAIZ = Path(__file__).resolve().parents[2]
SESSIONS = str(RAIZ / "sessions" / "*" / "products" / "*")
EXTS = (".png", ".jpg", ".jpeg")

# Las sesiones guardan en products/ algunas imágenes que NO son un producto:
# bodegones YA COMPUESTOS que alguien volvió a subir. Cuentan como SKU por
# nombre de archivo pero son composiciones enteras —una llegaba a 3504x3362 y
# traía su burbuja de zoom dibujada— así que ensucian dos cosas: la razón
# ancho/alto media que decide el reparto de filas, y cualquier inspección
# visual del resultado. Se excluyen.
NO_PRODUCTO = re.compile(r"bodeg[oó]n|composici|output", re.I)


def catalogo() -> list:
    """[{name, filepath, width, height, bytes}] de SKUs únicos, mayor primero."""
    mejor = {}
    for fp in glob.glob(SESSIONS):
        if not fp.lower().endswith(EXTS):
            continue
        sku = os.path.basename(fp)
        if NO_PRODUCTO.search(sku):
            continue
        sz = os.path.getsize(fp)
        if sku not in mejor or sz > mejor[sku][1]:
            mejor[sku] = (fp, sz)

    out = []
    for sku, (fp, sz) in mejor.items():
        try:
            with Image.open(fp) as im:
                w, h = im.size
        except Exception:
            continue
        out.append({"name": os.path.splitext(sku)[0], "filepath": fp,
                    "width": w, "height": h, "bytes": sz})
    out.sort(key=lambda r: -r["bytes"])
    return out


def set_de(n: int) -> list:
    """n productos variados: recorre el catálogo con paso para mezclar formatos
    (botellas altas, bolsas anchas) en vez de agarrar n botellas seguidas."""
    cat = catalogo()
    if len(cat) < n:
        raise RuntimeError(f"solo hay {len(cat)} SKUs únicos, se pidieron {n}")
    paso = max(1, len(cat) // n)
    sel, i = [], 0
    while len(sel) < n and i < len(cat):
        sel.append(cat[i])
        i += paso
    for c in cat:                      # rellenar si el paso se quedó corto
        if len(sel) >= n:
            break
        if c not in sel:
            sel.append(c)
    return sel[:n]


if __name__ == "__main__":
    cat = catalogo()
    print(f"SKUs únicos en disco: {len(cat)}")
    for n in (14, 16, 30, 60):
        try:
            s = set_de(n)
            ratios = [p["width"] / p["height"] for p in s]
            anchos = sum(1 for r in ratios if r > 1.35)
            print(f"  set de {n:2d}: ok — {anchos} formato ancho, {len(s)-anchos} vertical/cuadrado")
        except RuntimeError as e:
            print(f"  set de {n:2d}: {e}")
