"""¿Suben Sunburst o Flare el techo del motor generativo?

El motor usa gpt-image-2, que esta medido: bueno hasta 52 y COLAPSO en 60 (ver
la curva en image_gen.py). La pregunta de esta rama es si los modelos 2.5
mueven ese techo, porque es el unico limite duro que le queda al modo
generativo.

DOS DETALLES DE ACCESO que costaron encontrarlos:

1. `gpt-image-2.5-sunburst` y `gpt-image-2.5-flare` NO aparecen en
   /v1/models. Buscar ahi da falso negativo — parece que no existen.

2. Por `images.edit` (la via del motor) devuelven 403: "Your organization must
   be verified". Por Responses API con la herramienta image_generation
   funcionan sin verificar. Este script usa Responses por eso; si algun dia se
   verifica la organizacion, el motor puede quedarse en images.edit y solo
   cambiar la cadena del modelo.

USA set_de(), NO los productos de una sesion. Esto se aprendio gastando dos
generaciones: la carpeta products/ de una sesion de 60 tiene solo 4 imagenes
DISTINTAS repetidas once veces cada una, asi que pasarle products/[:45] no
prueba 45 productos — prueba 4 repetidos, y el modelo devuelve una rejilla
ordenada que parece un fallo y no lo es. set_de() deduplica por SKU entre
todas las sesiones: da 42 distintos para n=45 y 55 para n=60.

  GASTA API (genera imagenes). Requiere --go.
    python tests/bodegon/sunburst_25.py 45 52 60 --go
    python tests/bodegon/sunburst_25.py 45 --go --flare
"""
import base64
import os
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.chdir(RAIZ)

from dotenv import load_dotenv

load_dotenv()

from set60 import set_de
import image_gen

# El modelo de razonamiento que hospeda la herramienta. La imagen la hace el
# modelo del tool, no este.
ANFITRION = "gpt-6-astra"

# Limite propio de la via Responses que no existe en images.edit: cada
# input_image no puede pasar de 30000 "patches". El catalogo tiene una Fanta de
# 6600x10200 (67 Mpx, 24 MB) que pide 33354 y tumba la peticion entera con un
# 400 — no degrada, la rechaza. Asi que aqui hay que reducir antes de mandar.
#
# 1024 de lado mayor deja ~1024 patches, muy holgado, y sigue siendo mas
# resolucion de la que el producto va a ocupar en un lienzo de 1536x1024 con 45
# productos dentro. Se conserva PNG con alfa a proposito: el recorte es lo que
# el modelo necesita ver, y _to_jpeg_bytes de ai_proposal.py aplana sobre
# blanco porque sirve para analizar, no para generar.
LADO_MAX_ENTRADA = 1024

# Lo que la 2.5 abre respecto a gpt-image-2, y que el motor todavia no usa:
#
#   calidad   low, medium, high, XHIGH, MAX, auto   (xhigh y max son nuevos)
#   tamano    multiplos de 16, proporcion 1:3 a 3:1, hasta 3840 px por lado
#             y 8.294.400 px totales
#
# El motor esta fijo en 1536x1024 = 1,57 Mpx, o sea el 19% del maximo, y pide
# "high", el tercer nivel de seis. Toda la curva de 45 a 80 se midio con ESE
# ajuste conservador, asi que el techo de productos puede no ser del modelo
# sino de los pixeles que le dimos para dibujar las etiquetas.
TAMANOS = {
    "actual": "1536x1024",     # 1,57 Mpx — lo que usa el motor hoy
    "medio":  "2048x1360",     # 2,79 Mpx — 1,8x
    "alto":   "3072x2048",     # 6,29 Mpx — 4x
    "maximo": "3840x2160",     # 8,29 Mpx — el techo de la API
}


def _png_reducido(ruta: Path) -> bytes:
    from io import BytesIO

    from PIL import Image

    with Image.open(ruta) as im:
        im = im.convert("RGBA")
        if max(im.size) > LADO_MAX_ENTRADA:
            im.thumbnail((LADO_MAX_ENTRADA, LADO_MAX_ENTRADA), Image.LANCZOS)
        buf = BytesIO()
        im.save(buf, format="PNG", optimize=True)
        return buf.getvalue()


def generar(n: int, modelo: str, size: str = "1536x1024", quality: str = "high"):
    """Una generacion con N productos. Devuelve (segundos, ruta, diagnostico)."""
    from openai import OpenAI
    from PIL import Image

    prods = set_de(n)
    names = [p["name"] for p in prods]
    contenido = [{"type": "input_text",
                  "text": image_gen._build_generation_prompt(names, "")}]
    pesado = 0
    for p in prods:
        crudo = _png_reducido(Path(p["filepath"]))
        pesado += len(crudo)
        b64 = base64.b64encode(crudo).decode()
        contenido.append({"type": "input_image",
                          "image_url": f"data:image/png;base64,{b64}"})
    print(f"        entrada: {len(prods)} productos · {pesado / 1048576:.1f} MB "
          f"tras reducir a {LADO_MAX_ENTRADA} px", flush=True)

    cli = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    t = time.perf_counter()
    r = cli.responses.create(
        model=ANFITRION,
        input=[{"role": "user", "content": contenido}],
        tools=[{"type": "image_generation", "model": modelo,
                "quality": quality, "size": size, "background": "transparent"}],
    )
    seg = time.perf_counter() - t

    llamadas = [o for o in r.output if o.type == "image_generation_call"]
    if not llamadas or not llamadas[0].result:
        estado = llamadas[0].status if llamadas else "sin llamada"
        return seg, None, f"SIN IMAGEN (status {estado})"

    ruta = Path(f"/tmp/{modelo.split('-')[-1]}_{n}_{size}_{quality}.png")
    ruta.write_bytes(base64.b64decode(llamadas[0].result))
    im = Image.open(ruta)

    # El colapso de gpt-image-2 en n=60 se reconocia asi: devolvia una foto de
    # stock ajena, OPACA y ocupando todo el lienzo. Un bodegon compuesto llega
    # transparente y deja margen. No prueba que las etiquetas esten bien, pero
    # detecta el abandono sin mirar.
    diag = f"{im.size[0]}x{im.size[1]} · {ruta.stat().st_size // 1024} KB"
    if im.mode == "RGBA":
        alfa = im.split()[-1]
        transp = alfa.getextrema()[0] < 255
        bb = alfa.getbbox()
        diag += " · transparente" if transp else " · OPACA (revisar: posible abandono)"
        if bb:
            diag += (f" · contenido {round((bb[2]-bb[0])*100/im.width)}%"
                     f"x{round((bb[3]-bb[1])*100/im.height)}%")
    else:
        diag += f" · {im.mode} sin alfa (revisar: posible abandono)"
    return seg, ruta, diag


if __name__ == "__main__":
    ns = [int(a) for a in sys.argv[1:] if a.isdigit()] or [45, 52, 60]
    modelo = ("gpt-image-2.5-flare" if "--flare" in sys.argv
              else "gpt-image-2.5-sunburst")
    size = TAMANOS["actual"]
    for clave, valor in TAMANOS.items():
        if f"--{clave}" in sys.argv:
            size = valor
    quality = "high"
    for q in ("medium", "xhigh", "max"):
        if f"--{q}" in sys.argv:
            quality = q

    if "--go" not in sys.argv:
        print(f"Generaria {len(ns)} imagenes con {modelo} (n = {', '.join(map(str, ns))}),")
        print(f"a {size} y calidad {quality}. Cada una gasta una llamada de imagen.")
        print("Se cobra por token de salida, asi que subir el tamano sube el coste.")
        print("Agrega --go para ejecutarlo.")
        sys.exit(0)

    print(f"modelo: {modelo} · {size} · calidad {quality} · anfitrion: {ANFITRION}\n")
    for n in ns:
        try:
            seg, ruta, diag = generar(n, modelo, size=size, quality=quality)
            print(f"  n={n:<3} {seg:6.0f}s  {diag}")
            if ruta:
                print(f"        {ruta}")
        except Exception as e:
            print(f"  n={n:<3} ERROR {type(e).__name__}: {str(e)[:160]}")
