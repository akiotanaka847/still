"""¿Cuántos de los N productos decide REALMENTE la visora?

Llama a la IA de verdad y mide la cobertura antes de que `_parse_result`
rellene los huecos. Esa es la pregunta que hoy no se puede contestar mirando
la respuesta de la app: el relleno es silencioso y todo sale "completo".

  GASTA API. Requiere --go.
    python tests/bodegon/visora_60.py 60 --go
"""
import json
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

import ai_proposal as ap
from set60 import set_de

capturado = {}
_orig = ap._parse_result


def _spy(raw, names):
    """Intercepta la respuesta CRUDA para medir cobertura real."""
    capturado["raw"] = raw
    try:
        m = __import__("re").search(r"\{[\s\S]*\}", (raw or "").strip())
        crudo = json.loads(m.group(0)) if m else {}
    except Exception:
        crudo = {}
    capturado["order_ia"] = [n for n in (crudo.get("order") or []) if n in set(names)]
    capturado["sizes_ia"] = {k: v for k, v in (crudo.get("sizes") or {}).items() if k in set(names)}
    return _orig(raw, names)


ap._parse_result = _spy


def correr(n, provider=None):
    prods = set_de(n)
    names = [p["name"] for p in prods]
    prov = provider or os.environ.get("AI_PROVIDER", "openai")
    print(f"→ {n} productos · proveedor={prov}")

    t0 = time.time()
    try:
        res = ap.analyze_products(
            prods,
            groq_key=os.environ.get("GROQ_API_KEY") or None,
            gemini_key=os.environ.get("GEMINI_API_KEY") or None,
            anthropic_key=os.environ.get("ANTHROPIC_API_KEY") or None,
            openai_key=os.environ.get("OPENAI_API_KEY") or None,
            provider=prov,
        )
    except Exception as e:
        print(f"  ✗ FALLÓ tras {time.time()-t0:.1f}s: {type(e).__name__}: {str(e)[:200]}")
        return None
    dt = time.time() - t0

    oi = capturado.get("order_ia", [])
    si = capturado.get("sizes_ia", {})
    cov_o = 100 * len(oi) / n
    cov_s = 100 * len(si) / n
    vals = sorted(res["sizes"].values())
    rango = vals[-1] - vals[0] if vals else 0

    print(f"  latencia            {dt:.1f}s")
    print(f"  order de la IA      {len(oi):3d}/{n}  ({cov_o:.0f}%)")
    print(f"  sizes de la IA      {len(si):3d}/{n}  ({cov_s:.0f}%)")
    print(f"  rellenado en silencio {n-len(si):3d} productos")
    print(f"  hero                {res.get('hero','')[:40]}")
    print(f"  rango de escala     {rango:.3f}  (min {vals[0]:.3f} · max {vals[-1]:.3f})")
    print(f"  layouts elegidos    {res.get('layouts')}")
    veredicto = "✓ cubrió todo" if cov_s >= 99 else ("⚠ parcial" if cov_s > 50 else "✗ mayoría inventada por el código")
    print(f"  VEREDICTO           {veredicto}")
    return {"n": n, "cov_order": cov_o, "cov_sizes": cov_s, "latencia": dt, "rango": rango}


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    n = int(args[0]) if args else 60
    prov = args[1] if len(args) > 1 else None
    if "--go" not in sys.argv:
        print(__doc__)
        print(f"  (haría 1 llamada con {n} productos — agregá --go)")
        sys.exit(0)
    correr(n, prov)
