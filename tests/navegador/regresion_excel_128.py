"""Verifica los dos puntos del usuario:

1. Una promo cuyo dato NOMBRA una imagen que no existe se genera igual, SIN
   dibujar nada en ese lugar (antes estampaba CA_ERROR / "FALTA ELEMENTO").
2. El modulo original con el Excel de 128 casos sigue generando 128/128.
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
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8010"
SP = SALIDA + os.sep
EXCEL = os.path.join(RAIZ, "tests", "fixtures", "casos_prueba_con_subcasos.xlsx")

# Fila 1: pide imagenes que NO existen. Fila 2: no pide ninguna (control).
DATOS = {
    "name": "prueba assets faltantes",
    "rows": [
        {
            "ORIENTACION": "V",
            "CALLOUT": "NO_EXISTE_ESTE_CALLOUT.svg",
            "BRAND": "NO_EXISTE_ESTA_MARCA.svg",
            "FRASE APOYO": "ULTIMOS DIAS POR SOLO",
            "PESOS O PRECIO": "1",
            "DESCRIPCION": "Ruffles Mundialista Buffalo Bolzaza",
        },
        {
            "ORIENTACION": "V",
            "FRASE APOYO": "ULTIMOS DIAS POR SOLO",
            "PESOS O PRECIO": "1",
            "DESCRIPCION": "Ruffles Mundialista Buffalo Bolzaza",
        },
    ],
}
JSON_PATH = SP + "assets_faltantes.json"
open(JSON_PATH, "w").write(json.dumps(DATOS, ensure_ascii=False))

errores_js = []

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1500, "height": 1200})
    page.on("pageerror", lambda e: errores_js.append(str(e)))
    page.goto(BASE)
    page.request.post(f"{BASE}/api/auth/login",
                      data={"username": "testgen_still_ai", "password": "testpass123"})
    page.goto(BASE)
    page.wait_for_timeout(600)
    page.click("#nav-promo")
    page.wait_for_selector("#labInput", state="attached", timeout=15000)

    # ── 1. JSON que pide imagenes inexistentes ──────────────────────────────
    page.set_input_files("#labInput", JSON_PATH)
    page.wait_for_timeout(1500)
    page.click("#labGenerateBtn")
    page.wait_for_selector("#labPreviewWrap", state="visible", timeout=30000)
    page.wait_for_timeout(1500)

    svg = page.eval_on_selector("#labSvgHost svg", "el => el.outerHTML")
    n_imagenes = svg.count("<image")
    print("── FILA 1: pide 2 imagenes que no existen ──")
    print("   menciona CA_ERROR:", "CA_ERROR" in svg)
    print("   menciona 'FALTA ELEMENTO':", "FALTA ELEMENTO" in svg)
    print("   elementos <image> en el SVG:", n_imagenes)
    print("   se genero la promo (tiene texto):", "Ruffles" in svg or "RUFFLES" in svg.upper())

    log = page.eval_on_selector("#labLog", "el => el.innerText")
    avisos = [l for l in log.splitlines() if "no está en la biblioteca" in l]
    print("   avisos en el log:", len(avisos))
    for a in avisos:
        print("     ", a.strip())

    page.screenshot(path=SP + "sin_falta_elemento.png", clip={"x": 760, "y": 100, "width": 700, "height": 900})

    # ── 2. Regresion del modulo original con el Excel ───────────────────────
    print()
    print("── REGRESION: modulo original, Excel de 128 casos ──")
    page.set_input_files("#excelInput", EXCEL)
    page.wait_for_timeout(2500)
    page.click("#generateBatchBtn")
    page.wait_for_function(
        "() => { const t = document.querySelector('#batchTotal');"
        " return t && /OK/.test(t.innerText); }", timeout=600000)
    print("  ", page.eval_on_selector("#batchTotal", "el => el.innerText").strip())

    browser.close()

print()
print("errores JS:", errores_js if errores_js else "ninguno")
