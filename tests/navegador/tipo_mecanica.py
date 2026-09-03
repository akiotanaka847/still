"""TIPO_MECANICA eligiendo sola la mecanica, y el aviso de contradiccion."""
import os
import tempfile

# Rutas relativas al repo: estas pruebas nacieron en un directorio temporal de
# sesion y se trajeron acá para que sobrevivan. AQUI = tests/navegador/
AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(os.path.dirname(AQUI))
SALIDA = os.path.join(tempfile.gettempdir(), "still_pruebas")
os.makedirs(SALIDA, exist_ok=True)
import json, re
from playwright.sync_api import sync_playwright
BASE="http://127.0.0.1:8010"
SP = SALIDA + os.sep

CASOS = [
  ("Comunicación de imagen SIN precio", "Comunicación de imagen", ""),
  ("Comunicación de imagen CON precio", "Comunicación de imagen", "30"),
  ("SIMPLE con precio (control)",       "SIMPLE",                 "30"),
]
errs=[]
with sync_playwright() as p:
    b=p.chromium.launch(); page=b.new_page(viewport={"width":1500,"height":1200})
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto(BASE); page.request.post(f"{BASE}/api/auth/login",
        data={"username":"testgen_still_ai","password":"testpass123"})
    for etq, tipo, precio in CASOS:
        doc=[{"Reporte_Promociones":[{"PROMOCION_ID":"P1","TIPO_MECANICA":tipo,
            "CATEGORIA":"Higiene","PRECIO":precio,
            "PRODUCTO_DESC":"Azalea Papel Higienico Premium 4 Rollos",
            "DESCRIPCION_POS":"Azalea Papel Higienico Premium 4 Rollos"}]}]
        ruta=SP+"tm.json"; open(ruta,"w").write(json.dumps(doc,ensure_ascii=False))
        page.goto(BASE); page.wait_for_timeout(600); page.click("#nav-promo")
        page.wait_for_selector("#labInput", state="attached", timeout=15000)
        page.set_input_files("#labInput", ruta); page.wait_for_timeout(1800)
        r=page.evaluate("""async () => {
          window.PROMO_FLOW_DECLARADO = true;
          const f = LAB.rows[0].fields;
          const g = (c) => { const x = f.find(y => y.column === c); return x ? x.value : null; };
          const antes = document.getElementById('labLog').innerText.length;
          const svg = (await labRenderRow(f)).svgString;
          const logNuevo = document.getElementById('labLog').innerText.slice(antes);
          window.PROMO_FLOW_DECLARADO = false;
          const t = [...svg.matchAll(/>([^<>]*)<\\/(?:tspan|text)>/g)].map(m=>m[1]).join(' ');
          return {familia: g('PROMO_FAMILIA'), mecanica: g('PROMO_MECANICA'),
                  aSolo: t.includes('A SÓLO'), cu: t.includes('c/u'),
                  precio: /\\b30\\b/.test(t), log: logNuevo};
        }""")
        print(f"  {etq}")
        print(f"     familia={r['familia']}  mecánica elegida={r['mecanica'] or '(deducida)'}")
        print(f"     dibuja 'A SÓLO'={r['aSolo']}  'c/u'={r['cu']}  precio={r['precio']}")
        for l in r["log"].splitlines():
            if "⚠" in l: print("      ", l.strip())
    b.close()
print("errores JS:", errs if errs else "ninguno")
