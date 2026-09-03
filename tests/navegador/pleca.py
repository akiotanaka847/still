"""Verifica la pleca: se aplica si la subiste, va donde el usuario elige, y no
sale si no se subio. Sube un asset de PRUEBA y lo borra al final por API.

El borrado va por API a proposito: nada de dialogos de confirmacion del
navegador, para no tocar archivos reales del repo por accidente.
"""
import os
import tempfile

# Rutas relativas al repo: estas pruebas nacieron en un directorio temporal de
# sesion y se trajeron acá para que sobrevivan. AQUI = tests/navegador/
AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(os.path.dirname(AQUI))
SALIDA = os.path.join(tempfile.gettempdir(), "still_pruebas")
os.makedirs(SALIDA, exist_ok=True)
import json
import re
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8010"
SP = SALIDA + os.sep
PLECA = os.path.join(AQUI, "PLECA_PRUEBA.svg")
NOMBRE = "PLECA_PRUEBA.svg"

BASE_ROW = {
    "ORIENTACION": "V", "FRASE APOYO": "ULTIMOS DIAS POR SOLO",
    "PESOS O PRECIO": "1", "DESCRIPCION": "Ruffles Mundialista Buffalo",
}
CASOS = [
    ("CON pleca", {**BASE_ROW, "PLECA": NOMBRE}),
    ("SIN pleca", dict(BASE_ROW)),
    ("pleca inexistente", {**BASE_ROW, "PLECA": "NO_SUBI_ESTA_PLECA.svg"}),
]
for i, (etq, row) in enumerate(CASOS):
    open(SP + f"pleca_caso{i}.json", "w").write(
        json.dumps({"name": etq, "rows": [row]}, ensure_ascii=False))

errores = []


def datos_pleca(svg):
    """(y, alto) del <image> de la pleca dentro del SVG, o None."""
    for m in re.finditer(r"<image[^>]*>", svg):
        tag = m.group(0)
        if "0B7A3B" in tag or "PLECA" in tag:
            y = float(re.search(r'\by="([-\d.]+)"', tag).group(1))
            h = float(re.search(r'\bheight="([-\d.]+)"', tag).group(1))
            return y, h
    return None


with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1500, "height": 1200})
    page.on("pageerror", lambda e: errores.append(str(e)))
    page.goto(BASE)
    page.request.post(f"{BASE}/api/auth/login",
                      data={"username": "testgen_still_ai", "password": "testpass123"})

    r = page.request.post(
        f"{BASE}/api/promo-lab/kits/base/assets",
        multipart={"files": {"name": NOMBRE, "mimeType": "image/svg+xml",
                             "buffer": open(PLECA, "rb").read()},
                   "type": "pleca"})
    print("subida de la pleca de prueba:", r.status, r.json().get("saved"))

    try:
        for posicion in ("arriba", "abajo"):
            rp = page.request.put(f"{BASE}/api/promo-lab/kits/base/pleca-position",
                                  form={"position": posicion})
            assert rp.ok, rp.text()
            print()
            print(f"════════ pleca configurada {posicion.upper()} ════════")

            for i, (etiqueta, _) in enumerate(CASOS):
                page.goto(BASE)
                page.wait_for_timeout(600)
                page.click("#nav-promo")
                page.wait_for_selector("#labInput", state="attached", timeout=15000)
                page.set_input_files("#labInput", SP + f"pleca_caso{i}.json")
                page.wait_for_timeout(1400)
                page.click("#labGenerateBtn")
                page.wait_for_selector("#labPreviewWrap", state="visible", timeout=30000)
                page.wait_for_timeout(1200)

                svg = page.eval_on_selector("#labSvgHost svg", "el => el.outerHTML")
                pl = datos_pleca(svg)
                vb = re.search(r'viewBox="([-\d.eE ]+)"', svg).group(1).split()
                print(f"  {etiqueta}:  <image> en el SVG: {svg.count('<image')}")
                if pl:
                    y, h = pl
                    top, alto_total = float(vb[1]), float(vb[3])
                    centro_pieza = top + alto_total / 2
                    donde = "ARRIBA" if (y + h / 2) < centro_pieza else "ABAJO"
                    print(f"     pleca dibujada -> {donde}  (y={y:.0f}, alto={h:.0f};"
                          f" pieza {top:.0f}..{top + alto_total:.0f})")
                    page.query_selector("#labSvgHost").screenshot(
                        path=SP + f"pleca_{posicion}.png")
                else:
                    print("     pleca: NO se dibuja")

                log = page.eval_on_selector("#labLog", "el => el.innerText")
                for l in log.splitlines():
                    if "⚠" in l:
                        print("     aviso:", l.strip())
    finally:
        d = page.request.delete(f"{BASE}/api/promo-lab/kits/base/assets/{NOMBRE}")
        print()
        print("limpieza — borrado de la pleca de prueba:", d.status)
        page.request.put(f"{BASE}/api/promo-lab/kits/base/pleca-position",
                         form={"position": "arriba"})

    b.close()

print("errores JS:", errores if errores else "ninguno")
