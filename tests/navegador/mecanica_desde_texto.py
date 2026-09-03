"""La mecanica REDACTADA en la descripcion elige el dibujo."""
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
BASE="http://127.0.0.1:8010"
SP = SALIDA + os.sep
CASOS=[
 ("promopack gratis (frase de la guía)", "Higiene",
  "Compra 2 o mas Charolas termicas desechables Festivo 20pz llevate Gratis Botana BITZ Variedad 70 g"),
 ("promopack gratis, pero con ALCOHOL", "Cerveza",
  "Compra 2 o mas Cervezas llevate Gratis Botana BITZ Variedad 70 g"),
 ("simple normal (sin frase)", "Higiene",
  "12 Latas Bud Light de 330 ml por $146"),
]
errs=[]
with sync_playwright() as p:
    b=p.chromium.launch(); page=b.new_page(viewport={"width":1500,"height":1200})
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto(BASE); page.request.post(f"{BASE}/api/auth/login",
        data={"username":"testgen_still_ai","password":"testpass123"})
    for etq, cat, mec in CASOS:
        doc=[{"Reporte_Promociones":[{"PROMOCION_ID":"P1","CATEGORIA":cat,
          "TIPO_MECANICA":"Promopack","DESCRIPCION_MECANICA":mec,"PRECIO":"",
          "PRODUCTO_DESC":"Charolas termicas Festivo 20 pz",
          "DESCRIPCION_POS":"Charolas termicas Festivo 20 pz"}]}]
        ruta=SP+"mt.json"; open(ruta,"w").write(json.dumps(doc,ensure_ascii=False))
        page.goto(BASE); page.wait_for_timeout(600); page.click("#nav-promo")
        page.wait_for_selector("#labInput", state="attached", timeout=15000)
        page.set_input_files("#labInput", ruta); page.wait_for_timeout(1800)
        r=page.evaluate("""async () => {
          window.PROMO_FLOW_DECLARADO = true;
          const f = LAB.rows[0].fields;
          if (!f.find(x => x.column === 'DESCRIPCION 2'))
            f.push({column:'DESCRIPCION 2',column_normalized:'DESCRIPCION2',
                    type:'text',value:'Botana Bitz Variedad 70 g',runs:[]});
          const g = c => { const x=f.find(y=>y.column===c); return x?x.value:null; };
          const antes=document.getElementById('labLog').innerText.length;
          const svg=(await labRenderRow(f)).svgString;
          const lg=document.getElementById('labLog').innerText.slice(antes);
          window.PROMO_FLOW_DECLARADO=false;
          const t=[...svg.matchAll(/>([^<>]*)<\\/(?:tspan|text)>/g)].map(m=>m[1]).join(' ');
          return {detectada:g('PROMO_MECANICA_TEXTO'), alcohol:!!g('PROMO_ALCOHOL'),
                  compra:t.includes('COMPRA'), gratis:t.includes('GRATIS'),
                  imgs:(svg.match(/<image/g)||[]).length, log:lg};
        }""")
        print(f"  {etq}")
        print(f"     frase detectada={r['detectada']}  alcohol={r['alcohol']}")
        print(f"     dibuja COMPRA={r['compra']}  GRATIS={r['gratis']}  <image>={r['imgs']}")
        for l in r["log"].splitlines():
            if "⚠" in l: print("      ", l.strip())
    b.close()
print("errores JS:", errs if errs else "ninguno")
