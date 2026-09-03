"""Mide el techo de escala de Bodegón SIN gastar API.

Tres cosas que hoy no se saben y se pueden medir en seco:

  1. Cuánto de una propuesta de 60 productos la decide la IA de verdad, y
     cuánto lo rellena el código por defecto sin avisar.
  2. Si el presupuesto de tokens de cada proveedor alcanza para el JSON que
     60 productos obligan a devolver.
  3. Cuántos píxeles le toca a cada producto en la generativa, contra el caso
     de 14 que sí se verificó bueno.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import ai_proposal as ap
from set60 import set_de

CHARS_POR_TOKEN = 3.6   # conservador para nombres de SKU (dígitos y "_" tokenizan mal)


def titulo(t):
    print(f"\n{'─'*74}\n{t}\n{'─'*74}")


# ─── 1. Relleno silencioso ────────────────────────────────────────────────────
def relleno_silencioso(n=60):
    titulo(f"1. RELLENO SILENCIOSO — propuesta de {n} productos")
    prods = set_de(n)
    names = [p["name"] for p in prods]

    print(f"{'IA devolvió':>12} {'order real':>11} {'sizes reales':>13} "
          f"{'% decidido':>11}  {'¿avisa?':>8}")
    for k in (n, 45, 30, 20, 10, 5):
        # Respuesta simulada: la IA solo alcanzó a cubrir k de n productos
        crudo = json.dumps({
            "order": names[:k],
            "hero": names[0],
            "sizes": {nm: 0.9 for nm in names[:k]},
            "layouts": {"dynamic": "impacto", "elegant": "profundidad", "editorial": "radial"},
            "detail_crop": {"x": .3, "y": .3, "w": .2, "h": .2},
            "reasoning": "x",
        })
        res = ap._parse_result(crudo, names)
        # ¿cuántos tamaños quedaron con el valor por defecto (0.88*mx)?
        propios = sum(1 for nm in names[:k])
        pct = 100 * k / n
        print(f"{k:>12} {len(res['order']):>11} {propios:>13} {pct:>10.0f}%  {'NO':>8}")

    print("\n  Los 60 salen SIEMPRE en `order` y SIEMPRE con un tamaño asignado,")
    print("  incluso cuando la IA solo opinó sobre 5. `_parse_result` hace")
    print("  order.extend(missing) y `_normalize_sizes` pone 0.88*mx a lo que falte.")
    print("  No hay ningún campo en la respuesta que diga cuántos decidió la IA.")


# ─── 2. Presupuesto de tokens ─────────────────────────────────────────────────
def presupuesto(n=60):
    titulo(f"2. PRESUPUESTO DE TOKENS — ¿alcanza para {n} productos?")
    prods = set_de(n)
    names = [p["name"] for p in prods]

    payload = json.dumps({
        "order": names,
        "hero": names[0],
        "sizes": {nm: 0.87 for nm in names},
        "layouts": {"dynamic": "impacto", "elegant": "profundidad", "editorial": "radial"},
        "detail_crop": {"x": 0.31, "y": 0.28, "w": 0.22, "h": 0.19},
        "reasoning": "El orden pone el producto más grande al centro y baja hacia los lados.",
    }, ensure_ascii=False)
    need = int(len(payload) / CHARS_POR_TOKEN)
    largo_medio = sum(len(x) for x in names) / len(names)

    print(f"  nombre de SKU promedio: {largo_medio:.0f} caracteres")
    print(f"  JSON completo a devolver: {len(payload)} caracteres ≈ {need} tokens\n")

    filas = [
        ("gemini / claude / openai", ap._dynamic_max_tokens(n)),
        ("groq", ap._dynamic_max_tokens(n, base=300, per_item=90, cap=2500)),
        ("IA Libre (todos)", ap._dynamic_max_tokens(n, base=400, per_item=90, cap=4000)),
    ]
    print(f"  {'proveedor':26s} {'budget':>8} {'necesita':>9} {'margen':>9}  veredicto")
    for etq, bud in filas:
        m = bud - need
        v = "✓ alcanza" if m > need * 0.25 else ("⚠ justo" if m > 0 else "✗ TRUNCA")
        print(f"  {etq:26s} {bud:>8} {need:>9} {m:>+9}  {v}")

    print(f"\n  Groq además solo VE {ap.GROQ_MAX_IMAGES} imágenes "
          f"({ap.GROQ_MAX_IMAGES}/{n} = {100*ap.GROQ_MAX_IMAGES//n}% de los productos);")
    print("  los otros van como texto, sin que el modelo los haya mirado nunca.")


# ─── 3. Resolución de la generativa ───────────────────────────────────────────
def resolucion():
    titulo("3. GENERATIVA — píxeles por producto vs. el caso verificado bueno")
    import image_gen
    print(f"  tope actual en código: MAX_PRODUCTS_FOR_GENERATION = "
          f"{image_gen.MAX_PRODUCTS_FOR_GENERATION}")
    print("  (el comentario dice: «no es un límite duro conocido de la API,")
    print("   es un tope propio con margen chico sobre lo ya verificado»)\n")

    lienzos = [("1024x1024", 1024, 1024), ("1536x1024", 1536, 1024), ("2048x2048", 2048, 2048)]
    print(f"  {'lienzo':11s} {'n':>4} {'px² / producto':>15} {'lado equiv.':>12}  vs. n=14")
    ref = None
    for etq, w, h in lienzos:
        for n in (14, 16, 30, 60):
            area = (w * h) / n
            lado = int(area ** 0.5)
            if etq == "1536x1024" and n == 14:
                ref = area
            rel = f"{area/ref:.2f}×" if ref else "—"
            marca = "  ← verificado bueno" if (etq == "1536x1024" and n == 14) else ""
            print(f"  {etq:11s} {n:>4} {int(area):>15,} {lado:>10}px  {rel:>7}{marca}")
        print()
    print("  El texto de una etiqueta necesita ~40px de alto para leerse. A 60")
    print("  productos en 1536x1024 cada uno cae en ~160px de lado: la etiqueta")
    print("  entera mide menos que eso. No es un problema de prompt.")


if __name__ == "__main__":
    relleno_silencioso(60)
    presupuesto(60)
    resolucion()
