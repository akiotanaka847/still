"""Las mecanicas nuevas de la guia: que dibujen lo que la guia dice, y que la
regla de alcohol se cumpla (sin "Compra", "Llevate" ni "Gratis")."""
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


def doc_retail(categoria):
    return [{"Reporte_Promociones": [{
        "PROMOCION_ID": "P1", "CATEGORIA": categoria,
        "PRECIO": "36", "PRODUCTO_DESC": "Refresco Coca Cola 355 ml",
        "DESCRIPCION_POS": "Refresco Coca Cola 355 ml",
        "TEXTO APOYO": "LLEVA MAS PAGA MENOS",
    }]}]


def textos(svg):
    return " ".join(re.findall(r">([^<>]+)</tspan>|>([^<>]+)</text>", svg)
                    and [m for pair in re.findall(r">([^<>]*)</(?:tspan|text)>", svg) for m in [pair]]
                    or [])


errores = []
with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1500, "height": 1200})
    page.on("pageerror", lambda e: errores.append(str(e)))
    page.goto(BASE)
    page.request.post(f"{BASE}/api/auth/login",
                      data={"username": "testgen_still_ai", "password": "testpass123"})

    for etiqueta, categoria in (("SIN alcohol (Refrescos)", "Refrescos"),
                                ("CON alcohol (Cerveza)", "Cerveza")):
        ruta = SP + f"mec_{categoria}.json"
        open(ruta, "w").write(json.dumps(doc_retail(categoria), ensure_ascii=False))
        page.goto(BASE)
        page.wait_for_timeout(700)
        page.click("#nav-promo")
        page.wait_for_selector("#labInput", state="attached", timeout=15000)
        page.set_input_files("#labInput", ruta)
        page.wait_for_timeout(2000)

        # DESCRIPCION 2 no viene del origen retail: se inyecta para poder ver
        # las mecanicas de promopack, que la necesitan.
        page.evaluate("""() => {
          const f = LAB.rows[0].fields;
          if (!f.find(x => x.column === 'DESCRIPCION 2'))
            f.push({column: 'DESCRIPCION 2', column_normalized: 'DESCRIPCION2',
                    type: 'text', value: 'Botana Bitz Variedad 70 g', runs: []});
        }""")

        print()
        print(f"════════ {etiqueta} ════════")
        marcado = page.evaluate("() => !!LAB.rows[0].fields.find(f => f.column === 'PROMO_ALCOHOL')")
        print(f"  marcada como alcohol por el backend: {marcado}")

        for mecanica in ("comunicacion_precio_regular", "promopack_suma", "promopack_gratis"):
            r = page.evaluate("""async (mec) => {
              window.PROMO_FLOW_DECLARADO = true;
              window.PROMO_MECANICA_FORZADA = mec;
              const antes = document.getElementById('labLog').innerText.length;
              let svg = '', err = null;
              try { svg = (await labRenderRow(LAB.rows[0].fields)).svgString; }
              catch (e) { err = String(e); }
              const logNuevo = document.getElementById('labLog').innerText.slice(antes);
              window.PROMO_FLOW_DECLARADO = false;
              window.PROMO_MECANICA_FORZADA = null;
              const t = [...svg.matchAll(/>([^<>]*)<\\/(?:tspan|text)>/g)].map(m => m[1]).join(' ');
              return {textos: t, imagenes: (svg.match(/<image/g) || []).length, log: logNuevo, err};
            }""", mecanica)
            if r["err"]:
                print(f"  {mecanica:<30} ERROR: {r['err'][:90]}")
                continue
            t = r["textos"]
            marcas = []
            for palabra in ("A SÓLO", "c/u", "COMPRA", "GRATIS", "+"):
                if palabra in t:
                    marcas.append(palabra)
            print(f"  {mecanica:<30} textos={marcas}  <image>={r['imagenes']}")
            for l in r["log"].splitlines():
                if "⚠" in l:
                    print(f"       {l.strip()}")
    b.close()

print()
print("errores JS:", errores if errores else "ninguno")
