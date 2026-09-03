"""La prueba que decide si el motor declarativo sirve: para las 128 filas del
Excel de casos, generar el SVG por el camino ESCRITO A MANO y por el DECLARADO,
y comparar caracter por caracter. Si no son identicos, la declaracion no
reproduce el comportamiento y no se puede confiar en ella.
"""
import os
import tempfile

# Rutas relativas al repo: estas pruebas nacieron en un directorio temporal de
# sesion y se trajeron acá para que sobrevivan. AQUI = tests/navegador/
AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(os.path.dirname(AQUI))
SALIDA = os.path.join(tempfile.gettempdir(), "still_pruebas")
os.makedirs(SALIDA, exist_ok=True)
import difflib
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8010"
EXCEL = os.path.join(RAIZ, "tests", "fixtures", "casos_prueba_con_subcasos.xlsx")

errores = []
with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1500, "height": 1200})
    page.on("pageerror", lambda e: errores.append(str(e)))
    page.goto(BASE)
    page.request.post(f"{BASE}/api/auth/login",
                      data={"username": "testgen_still_ai", "password": "testpass123"})
    page.goto(BASE)
    page.wait_for_timeout(700)
    page.click("#nav-promo")
    page.wait_for_selector("#excelInput", state="attached", timeout=15000)
    # Las filas no quedan en el estado (se piden y se consumen), asi que se
    # piden igual que lo hace la pantalla.
    with open(EXCEL, "rb") as fh:
        contenido = fh.read()
    r = page.request.post(f"{BASE}/api/promo/read-excel-batch",
        multipart={"file": {"name": "casos.xlsx",
                            "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            "buffer": contenido}})
    datos = r.json()
    page.evaluate("f => { window.__FILAS = f; }", datos["rows"])
    total = len(datos["rows"])
    print("filas cargadas:", total)

    resultado = page.evaluate("""async () => {
      const filas = window.__FILAS || [];
      const out = {iguales: 0, distintas: [], errores: [], porMecanica: {}};
      for (let i = 0; i < filas.length; i++) {
        const fields = filas[i].fields;
        let tpl;
        try { tpl = await templateForRow(fields); }
        catch (e) { out.errores.push({i, e: String(e)}); continue; }

        // qué mecánica le toca, para el desglose
        const raw = (c) => fields.find(f => normalize(f.column) === normalize(c));
        const mec = raw('PROMO_DOBLE_DESCRIPCION') ? 'doble_descripcion'
                  : raw('PROMO_SEGUNDO_POR')       ? 'segundo_por'
                  : raw('PROMO_SOLO_DESCRIPCION')  ? 'comunicacion_imagen'
                  : raw('PROMO_SOLO_FRASE_DESC')   ? 'simple_descripcion_larga'
                  : 'simple';
        const clave = tpl.orientation + ' / ' + mec;
        out.porMecanica[clave] = (out.porMecanica[clave] || 0) + 1;
        // Cuenta de verdad cuantas filas TOMARON el camino declarativo
        const decl = tpl.orientation === 'horizontal'
          ? ['simple','simple_descripcion_larga'].includes(mec)
          : ['simple','simple_descripcion_larga','comunicacion_imagen'].includes(mec);
        if (decl) out.ejercitadas = (out.ejercitadas || 0) + 1;

        try {
          const mapping = computeMapping(fields, tpl.fields);
          window.PROMO_FLOW_DECLARADO = false;
          const aMano = (await buildSvgWithMaxFill(tpl, mapping, fields)).svgString;
          window.PROMO_FLOW_DECLARADO = true;
          const declarado = (await buildSvgWithMaxFill(tpl, mapping, fields)).svgString;
          window.PROMO_FLOW_DECLARADO = false;
          if (aMano === declarado) out.iguales++;
          else out.distintas.push({i, mec, aMano, declarado});
        } catch (e) {
          out.errores.push({i, e: String(e)});
        }
      }
      return out;
    }""")

    print()
    print("orientación / mecánica de cada fila:")
    for k, v in sorted(resultado["porMecanica"].items(), key=lambda x: -x[1]):
        print(f"   {v:>4}x  {k}")
    print()
    print(f"SVG IDÉNTICOS por los dos caminos: {resultado['iguales']} de {total}")
    ej = resultado.get("ejercitadas", 0)
    print(f"filas que REALMENTE tomaron el camino declarativo: {ej}")
    print(f"filas iguales por definición (van a mano en ambos): {total - ej}")
    print(f"distintos: {len(resultado['distintas'])}   errores: {len(resultado['errores'])}")

    for d in resultado["distintas"][:2]:
        print()
        print(f"── primera diferencia (fila {d['i']}, mecánica {d['mec']}) ──")
        a = d["aMano"].splitlines()
        c = d["declarado"].splitlines()
        for l in list(difflib.unified_diff(a, c, "a_mano", "declarado", lineterm=""))[:14]:
            print("   ", l[:150])
    for e in resultado["errores"][:3]:
        print("   ERROR fila", e["i"], ":", e["e"][:160])

    b.close()

print()
print("errores JS:", errores if errores else "ninguno")
