#!/usr/bin/env python3
"""Convierte las tipografias de marca de OTF a WOFF2 para servirlas en la web.

Las fuentes son Goldplay y Goldplay Alt, del Q.Strauss Design System. Sus
tokens dicen para que sirve cada una:

    --font-display: "Goldplay Alt"    titulares, logotipos
    --font-body:    "Goldplay"        texto corrido

Por que WOFF2 y no el OTF tal cual: el design system carga los .otf
directamente, que vale para una pagina interna pero no para una aplicacion
que se abre a diario. Un OTF pesa 88 KB; el mismo corte en WOFF2 baja a un
tercio, porque WOFF2 lleva compresion Brotli dentro del formato y el
navegador la descomprime sola.

Se convierten SOLO los cortes que la interfaz usa de verdad. Contados en el
HTML: font-semibold 58 veces, font-bold 31, font-medium 27, font-black 9.
Meter los 28 ficheros (14 pesos x redonda e italica) serian mas de 2 MB para
que el 80% no se llegue a pedir nunca.

Uso:  .venv/bin/python tools/fuentes.py
"""
import pathlib
import shutil
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent
ORIGEN = pathlib.Path.home() / "Downloads" / "Q.Strauss Design System" / "uploads"
DESTINO = RAIZ / "static" / "fonts"

# (fichero de origen, nombre de salida, familia CSS, peso)
CORTES = [
    # Texto: los cuatro pesos que la interfaz pide
    ("Goldplay Regular.otf",   "goldplay-400.woff2",     "Goldplay",     400),
    ("Goldplay Medium.otf",    "goldplay-500.woff2",     "Goldplay",     500),
    ("Goldplay SemiBold.otf",  "goldplay-600.woff2",     "Goldplay",     600),
    ("Goldplay Bold.otf",      "goldplay-700.woff2",     "Goldplay",     700),
    # Display: titulares y logotipo
    ("Goldplay Alt Bold.otf",  "goldplay-alt-700.woff2", "Goldplay Alt", 700),
    ("Goldplay Alt Black.otf", "goldplay-alt-900.woff2", "Goldplay Alt", 900),
]


# Se recortan los caracteres, que es la otra mitad del ahorro. Las fuentes
# traen 365 glifos (sin cirilico ni griego, ya son latinas), y de esos la
# interfaz solo necesita ASCII, Latin-1 y puntuacion: ingles y espanol
# completos. Quitar Latin Extended-A —los acentos de Europa del Este— baja
# cada corte a la mitad otra vez.
#
# U+0131 es obligatorio y facil de perder de vista: es la i SIN PUNTO, que el
# logotipo usa en "Stıll". Si se cae, el navegador la saca de la fuente de
# sistema y la palabra queda descuadrada.
UNICODES = ",".join([
    "U+0020-007E",     # ASCII visible
    "U+00A0-00FF",     # Latin-1: acentos del espanol, ¿ ¡ ñ ü
    "U+0131",          # i sin punto — el logotipo
    "U+0152-0153",     # Œ œ
    "U+2000-206F",     # espacios, guiones, comillas tipograficas
    "U+20AC",          # €
    "U+2122",          # ™
    "U+2212",          # menos tipografico
])
# Nota: NO se piden flechas (U+2190-2193). Goldplay no las trae, ni la
# redonda ni la Alt — comprobado sobre el OTF original. Cualquier flecha
# escrita como texto caeria a la fuente del sistema y se notaria al lado de
# Goldplay, asi que en la interfaz van como SVG.


def main() -> int:
    if not ORIGEN.is_dir():
        print(f"no encuentro las fuentes en {ORIGEN}", file=sys.stderr)
        return 2

    from fontTools.subset import main as subset

    DESTINO.mkdir(parents=True, exist_ok=True)
    total_antes = total_despues = 0

    for fichero, salida, familia, peso in CORTES:
        src = ORIGEN / fichero
        if not src.exists():
            print(f"  FALTA {fichero}", file=sys.stderr)
            return 1
        dst = DESTINO / salida
        subset([
            str(src),
            f"--unicodes={UNICODES}",
            "--flavor=woff2",
            "--layout-features=kern,liga",   # kerning y ligaduras, que se notan
            f"--output-file={dst}",
        ])
        antes, despues = src.stat().st_size, dst.stat().st_size
        total_antes += antes
        total_despues += despues
        print(
            f"  {salida:<24} {familia:<13} {peso}  "
            f"{antes // 1024:>3} KB -> {despues // 1024:>3} KB"
        )

    print(
        f"\n  total: {total_antes // 1024} KB -> {total_despues // 1024} KB "
        f"({100 - total_despues * 100 // total_antes}% menos)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
