"""
Still Studio — entrypoint de composición por LLAMADA DIRECTA (sin servidor web).

Esta es la vía que usa el agente de qaio: en vez de levantar el servidor FastAPI
(`uvicorn main:app`) y consumir su API HTTP — lo que arrastraba la página web y el
login de StillAI —, el agente llama a este script, que invoca directo las tres
funciones del motor determinista:

    normalize_image()  →  quita fondo (si opaca) + recorta al ras   (normalize.py)
    compute_layout()   →  orden, escala, posiciones, filas, sombras (layout.py)
    render_png()       →  compone y guarda el PNG final             (renderer.py)

Sin uvicorn, sin localhost, sin login. El servidor web sigue existiendo (main.py)
para la futura versión SaaS por navegador (Fase 2), pero el agente no lo necesita.

Frontera sagrada: la IA (el agente) decide SOLO {order, hero, sizes}; este script
no compone a mano, solo orquesta las llamadas al motor.

Uso:
    python compose.py --spec spec.json --out salida.png
    python compose.py --spec spec.json            # imprime solo el JSON de resultado

spec.json:
{
  "products": [                          # EN ORDEN (el agente ya decidió el orden)
    {"name": "BOTELLA_1L", "path": "/ruta/botella.png", "size": 1.0},
    {"name": "SERUM_30ML", "path": "/ruta/serum.jpg",   "size": 0.35}
  ],
  "hero": "BOTELLA_1L",                   # informativo; el orden ya viene aplicado
  "background": null,                     # o "/ruta/fondo.png"
  "strategy": "ai_depth",                 # ai | ai_depth | ai_shadow | geométricas
  "base_height": 760,                     # resolución (300–2000)
  "item_gap": 40,                         # separación
  "aspect_w": 0, "aspect_h": 0,           # 0/0 = auto
  "bg_method": "rembg"                    # rembg | native (auto-detecta alpha igual)
}

Salida (stdout, JSON): {ok, output, canvas, count, failures, degraded, reasoning?}
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from layout import compute_layout
from renderer import render_png
from normalize import BgMethod, NormalizeStatus, normalize_image


def _bg_method(value: Optional[str]) -> BgMethod:
    """Mapea el método pedido; cualquier valor desconocido cae a rembg (default F1)."""
    try:
        return BgMethod(value) if value else BgMethod.REMBG
    except ValueError:
        return BgMethod.REMBG


def compose(spec: Dict[str, Any], out_path: str, work_dir: Optional[str] = None) -> Dict[str, Any]:
    """
    Normaliza cada producto, calcula el layout y renderiza el PNG.

    Devuelve un dict serializable con el resultado. NO lanza por fallos esperados
    (imagen ilegible, cutout vacío): los reporta en `failures` y sigue con el resto.
    Si NINGÚN producto sobrevive la normalización, marca ok=False.
    """
    products: List[Dict[str, Any]] = spec.get("products") or []
    if not products:
        return {"ok": False, "error": "spec sin 'products'"}

    bg_method = _bg_method(spec.get("bg_method"))
    workdir = Path(work_dir or tempfile.mkdtemp(prefix="still-compose-"))
    workdir.mkdir(parents=True, exist_ok=True)

    images: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    degraded: List[Dict[str, Any]] = []

    # 1) Normalización en ingesta (una vez por producto), preservando el ORDEN del spec.
    for i, p in enumerate(products):
        name = str(p.get("name") or f"P{i+1}")
        src = p.get("path")
        if not src or not Path(src).exists():
            failures.append({"name": name, "error": f"archivo no encontrado: {src}"})
            continue

        packshot = workdir / f"{name}.packshot.png"
        res = normalize_image(str(src), str(packshot), bg_method=bg_method, name=name)

        if res.status == NormalizeStatus.FAILED:
            # Falla explícita: NO se compone una caja cruda aguas abajo.
            failures.append({"name": name, "error": res.error})
            continue
        if res.status == NormalizeStatus.DEGRADED:
            degraded.append({"name": name, "confidence": res.confidence})

        images.append({
            "name": name,
            "filepath": res.packshot_path,
            "width": res.features.width,
            "height": res.features.height,
            # 'scale' = tamaño real relativo decidido por la visión del agente.
            # Solo se adjunta si el spec lo trae (estrategias ai*); si no, el motor
            # escala por altura como en las estrategias geométricas.
            **({"scale": float(p["size"])} if p.get("size") is not None else {}),
        })

    if not images:
        return {"ok": False, "error": "ningún producto sobrevivió la normalización",
                "failures": failures}

    # 2) Layout determinista (orden ya aplicado por el agente vía estrategias ai*).
    layout = compute_layout(
        images=images,
        sort_strategy=str(spec.get("strategy", "ai_depth")),
        base_height=int(spec.get("base_height", 760)),
        item_gap=int(spec.get("item_gap", 40)),
        aspect_w=int(spec.get("aspect_w", 0)),
        aspect_h=int(spec.get("aspect_h", 0)),
    )

    # 3) Render final.
    background = spec.get("background")
    if background and not Path(background).exists():
        background = None
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    render_png(layout, out_path, background_path=background)

    return {
        "ok": True,
        "output": out_path,
        "canvas": {"width": layout["canvas_width"], "height": layout["canvas_height"]},
        "strategy": layout["sort_strategy"],
        "count": len(layout["items"]),
        "hero": spec.get("hero"),
        "failures": failures,
        "degraded": degraded,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Compone un bodegón llamando al motor directo.")
    ap.add_argument("--spec", required=True, help="ruta al spec.json")
    ap.add_argument("--out", help="ruta del PNG de salida (default: outputs/bodegon.png)")
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
