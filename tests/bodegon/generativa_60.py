"""¿Aguanta la generativa (gpt-image-2) N productos de entrada?

El tope de 16 en image_gen.py es propio, no de la API — el comentario lo dice.
Esto averigua dónde está el límite REAL: cuántos archivos acepta
`images.edit` y con qué lienzo.

  GASTA API (genera imágenes). Requiere --go.
    python tests/bodegon/generativa_60.py 60 --go            # sondeo barato
    python tests/bodegon/generativa_60.py 60 --go --alta     # calidad real
"""
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


def sondeo(n, size="1024x1024", quality="low", out=None):
    """Llama a images.edit directamente, salteando el tope propio de image_gen."""
    from openai import OpenAI
    prods = set_de(n)
    names = [p["name"] for p in prods]
    prompt = image_gen._build_generation_prompt(names, "")

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    files = [open(p["filepath"], "rb") for p in prods]
    t0 = time.time()
    try:
        r = client.images.edit(model="gpt-image-2", image=files, prompt=prompt,
                               size=size, quality=quality, background="transparent")
    except Exception as e:
        print(f"  ✗ n={n:3d} {size} q={quality:4s} → {type(e).__name__}: {str(e)[:160]}")
        return False
    finally:
        for f in files:
            f.close()
    dt = time.time() - t0
    import base64
    png = base64.b64decode(r.data[0].b64_json)
    print(f"  ✓ n={n:3d} {size} q={quality:4s} → {len(png)/1024:.0f} KB en {dt:.0f}s")
    if out:
        Path(out).write_bytes(png)
        print(f"      guardado en {out}")
    return True


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    n = int(args[0]) if args else 60
    if "--go" not in sys.argv:
        print(__doc__)
        sys.exit(0)
    alta = "--alta" in sys.argv
    size = "1536x1024" if alta else "1024x1024"
    qual = "high" if alta else "low"
    salida = f"/tmp/gen_{n}_{qual}.png"
    sondeo(n, size, qual, salida)
