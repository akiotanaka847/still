#!/usr/bin/env python3
"""Genera los favicons a partir de static/still-mark.png.

Por que existe: el favicon ERA el logo real, solo que reducido, y a 16 px no
se leia. La "S" del logo es un trazo con degradado suave sobre un fondo de
cuatro colores; al bajar a tamano de pestana el trazo pierde contraste contra
su propio fondo y el icono queda como una mancha violeta.

El arreglo NO es cambiar el fondo — eso costaba la identidad de la marca, que
es justo el cian y el magenta. El arreglo es pintar el glifo en BLANCO PURO
en los tamanos chicos. Se conserva el degradado real y la letra se lee.

Reparto por tamano, que es la practica normal: el arte detallado aguanta en
grande, en 16 px solo sobrevive una silueta.

    16 / 32 / 48   glifo simplificado en blanco puro
    180 / 512      el arte original, sin tocar
    favicon.ico    multi-tamano 16+32+48 (antes era un solo 256, que los
                   navegadores reducian mal y era el origen del problema)

Uso:  .venv/bin/python tools/favicons.py
"""
import pathlib
import sys

from PIL import Image, ImageFilter

RAIZ = pathlib.Path(__file__).resolve().parent.parent
ORIGEN = RAIZ / "static" / "still-mark.png"
DESTINO = RAIZ / "static"

# El fondo del logo siempre tiene un canal bajo (los cuatro extremos del
# degradado son 46,45,216 / 4,188,222 / 174,59,252 / 26,32,179). El glifo
# —blanco y su barra lavanda— tiene los tres canales altos. Asi que min(r,g,b)
# separa letra de fondo, y el histograma sale claramente bimodal: el fondo por
# debajo de 120, el glifo por encima.
UMBRAL_GLIFO = 120

CHICOS = (16, 32, 48)      # se leen: glifo en blanco puro
GRANDES = (180, 512)       # arte original


def mascara_del_glifo(im: Image.Image) -> Image.Image:
    ancho, alto = im.size
    px = im.load()
    m = Image.new("L", (ancho, alto), 0)
    mp = m.load()
    for y in range(alto):
        for x in range(ancho):
            r, g, b, a = px[x, y]
            if a > 128 and min(r, g, b) >= UMBRAL_GLIFO:
                mp[x, y] = 255
    return m


def version_legible(im: Image.Image, px_final: int) -> Image.Image:
    """El logo con su degradado real, pero la letra en blanco pleno."""
    glifo = mascara_del_glifo(im)
    # Un pase de dilatacion: al reducir a 16 px el trazo adelgaza y se corta.
    glifo = glifo.filter(ImageFilter.MaxFilter(5))
    letra = Image.new("RGBA", im.size, (255, 255, 255, 0))
    letra.putalpha(glifo)
    out = im.copy()
    out.alpha_composite(letra)
    return out.resize((px_final, px_final), Image.LANCZOS)


def main() -> int:
    if not ORIGEN.exists():
        print(f"falta {ORIGEN}", file=sys.stderr)
        return 2
    src = Image.open(ORIGEN).convert("RGBA")
    print(f"origen: {ORIGEN.name} {src.size[0]}x{src.size[1]}")

    for n in CHICOS:
        f = DESTINO / f"favicon-{n}.png"
        version_legible(src, n).save(f, "PNG", optimize=True)
        print(f"  {f.name:<20} {n}x{n}  glifo en blanco  {f.stat().st_size:>7} B")

    for n in GRANDES:
        f = DESTINO / f"favicon-{n}.png"
        src.resize((n, n), Image.LANCZOS).save(f, "PNG", optimize=True)
        print(f"  {f.name:<20} {n}x{n}  arte original    {f.stat().st_size:>7} B")

    ico = DESTINO / "favicon.ico"
    version_legible(src, 48).save(
        ico, "ICO", sizes=[(16, 16), (32, 32), (48, 48)]
    )
    print(f"  {ico.name:<20} 16+32+48         {ico.stat().st_size:>7} B")
    return 0


if __name__ == "__main__":
    sys.exit(main())
