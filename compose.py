"""
Still Studio — entrypoint de composición por LLAMADA DIRECTA (sin servidor web).

Esta es la vía que usa el agente de qaio: en vez de levantar el servidor FastAPI
(`uvicorn main:app`) y consumir su API HTTP — lo que arrastraba la página web y el
login de StillAI —, el agente llama a este script, que invoca directo las funciones
del motor determinista:

    normalize_image()  →  quita fondo (si opaca) + recorta al ras   (normalize.py)
    compute_layout()   →  orden, escala, posiciones, filas, sombras (layout.py)
    render_png()       →  compone y guarda el PNG final             (renderer.py)

Sin uvicorn, sin localhost, sin login. El servidor web (main.py) queda para la
futura versión SaaS por navegador (Fase 2); el agente no lo necesita.

PROPUESTAS DE ESTILO: una corrida puede generar VARIOS estilos de layout a la vez
(como la página original mostraba para comparar). La normalización (quitar fondo +
recorte) se hace UNA sola vez y se comparte entre todos los estilos.

Fondo transparente: si `background` es null, el lienzo final es 100% transparente
(RGBA, alfa 0). Solo se pega un fondo si se entrega `background`.

Frontera sagrada: la IA (el agente) decide SOLO {order, hero, sizes}; este script
no compone a mano, solo orquesta las llamadas al motor.

Uso:
    python compose.py --spec spec.json --out outputs/grupo/bodegon.png

spec.json:
{
  "products": [                          # EN ORDEN (el agente ya decidió el orden)
    {"name": "BOTELLA_1L", "path": "/ruta/botella.png", "size": 1.0},
    {"name": "SERUM_30ML", "path": "/ruta/serum.jpg",   "size": 0.35}
  ],
  "hero": "BOTELLA_1L",
  "background": null,                     # null = TRANSPARENTE; o "/ruta/fondo.png"
  "strategies": ["ai_depth","ai","ai_shadow"],  # VARIOS estilos -> varios PNG
  "strategy": "ai_depth",                 # (alternativa: un solo estilo)
  "base_height": 760,
  "item_gap": 40,
  "aspect_w": 0, "aspect_h": 0,
  "bg_method": "rembg"                    # rembg | native (auto-detecta alpha igual)
}

Estilos válidos: ai, ai_depth, ai_shadow (respetan tu orden+tamaños),
auto, centrado, sombra, profundidad, profundidad_xl, profundidad_grad, uniforme.

Salida (stdout, JSON): {ok, proposals:[{strategy,path,canvas}], transparent, failures, degraded}
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from layout import compute_layout, SORT_STRATEGIES
from renderer import render_png
from normalize import BgMethod, NormalizeStatus, normalize_image


def _bg_method(value: Optional[str]) -> BgMethod:
    """Mapea el método pedido; cualquier valor desconocido cae a rembg (default F1)."""
    try:
        return BgMethod(value) if value else BgMethod.REMBG
    except ValueError:
        return BgMethod.REMBG


def _strategies(spec: Dict[str, Any]) -> List[str]:
    """Lista de estilos a generar. Acepta `strategies` (varios) o `strategy` (uno)."""
    raw = spec.get("strategies") or ([spec["strategy"]] if spec.get("strategy") else ["ai_depth"])
    # Filtra a estilos reales del motor; preserva orden y descarta duplicados.
    seen, out = set(), []
    for s in raw:
        if s in SORT_STRATEGIES and s not in seen:
            seen.add(s); out.append(s)
    return out or ["ai_depth"]


def _normalize_products(products: List[Dict[str, Any]], bg_method: BgMethod,
                        workdir: Path) -> Any:
    """Normaliza cada producto UNA vez (compartido entre estilos). Devuelve (images, failures, degraded)."""
    images: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    degraded: List[Dict[str, Any]] = []
    for i, p in enumerate(products):
        name = str(p.get("name") or f"P{i+1}")
        src = p.get("path")
        if not src or not Path(src).exists():
            failures.append({"name": name, "error": f"archivo no encontrado: {src}"})
            continue
        packshot = workdir / f"{name}.packshot.png"
        res = normalize_image(str(src), str(packshot), bg_method=bg_method, name=name)
        if res.status == NormalizeStatus.FAILED:
            failures.append({"name": name, "error": res.error})
            continue
        if res.status == NormalizeStatus.DEGRADED:
            degraded.append({"name": name, "confidence": res.confidence})
        images.append({
            "name": name,
            "filepath": res.packshot_path,
            "width": res.features.width,
            "height": res.features.height,
            **({"scale": float(p["size"])} if p.get("size") is not None else {}),
        })
    return images, failures, degraded


def compose(spec: Dict[str, Any], out_path: str, work_dir: Optional[str] = None) -> Dict[str, Any]:
    """
    Normaliza una vez y genera UNA o VARIAS propuestas de estilo.

    `out_path` es la ruta base. Con un solo estilo escribe ahí; con varios escribe
    `<stem>_<estilo><ext>` en la misma carpeta. NO lanza por fallos esperados; los
    reporta en `failures`.
    """
    products: List[Dict[str, Any]] = spec.get("products") or []
    if not products:
        return {"ok": False, "error": "spec sin 'products'"}

    bg_method = _bg_method(spec.get("bg_method"))
    workdir = Path(work_dir or tempfile.mkdtemp(prefix="still-compose-"))
    workdir.mkdir(parents=True, exist_ok=True)

    images, failures, degraded = _normalize_products(products, bg_method, workdir)
    if not images:
        return {"ok": False, "error": "ningún producto sobrevivió la normalización",
                "failures": failures}

    background = spec.get("background")
    if background and not Path(background).exists():
        background = None
    transparent = background is None

    base = Path(out_path)
    base.parent.mkdir(parents=True, exist_ok=True)
    strategies = _strategies(spec)
    multi = len(strategies) > 1

    proposals: List[Dict[str, Any]] = []
    for strat in strategies:
        layout = compute_layout(
            images=images,
            sort_strategy=strat,
            base_height=int(spec.get("base_height", 760)),
            item_gap=int(spec.get("item_gap", 40)),
            aspect_w=int(spec.get("aspect_w", 0)),
            aspect_h=int(spec.get("aspect_h", 0)),
            shadow=bool(spec.get("shadow", False)),   # APAGADA por defecto (cutout limpio)
        )
        outp = base.with_name(f"{base.stem}_{strat}{base.suffix}") if multi else base
        render_png(layout, str(outp), background_path=background)
        proposals.append({
            "strategy": strat,
            "path": str(outp),
            "canvas": {"width": layout["canvas_width"], "height": layout["canvas_height"]},
        })

    return {
        "ok": True,
        "proposals": proposals,
        "transparent": transparent,   # True = fondo 100% transparente (sin background)
        "hero": spec.get("hero"),
        "count": len(images),
        "failures": failures,
        "degraded": degraded,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Compone bodegón(es) llamando al motor directo.")
    ap.add_argument("--spec", required=True, help="ruta al spec.json")
    ap.add_argument("--out", help="ruta base del PNG (default: outputs/bodegon.png)")
    args = ap.parse_args(argv)

    try:
        spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"spec ilegible: {e}"}))
        return 1

    out_path = args.out or str(Path("outputs") / "bodegon.png")
    result = compose(spec, out_path)
    print(json.dumps(result, ensure_ascii=False, default=_json_default))
    return 0 if result.get("ok") else 1


def _json_default(o: Any) -> Any:
    if dataclasses.is_dataclass(o):
        return dataclasses.asdict(o)
    return str(o)


if __name__ == "__main__":
    sys.exit(main())
