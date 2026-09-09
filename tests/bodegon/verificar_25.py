#!/usr/bin/env python3
"""¿Ya puede la cuenta usar los modelos 2.5 por images.edit?

Los modelos gpt-image-2.5-sunburst y -flare requieren que la ORGANIZACION este
verificada. Sin verificar, images.edit —la via del motor— devuelve 403. Por
Responses API funcionan igual, pero esa via tiene sus propios costes (hay que
reducir la entrada a 30000 patches por imagen), asi que lo que interesa saber
es cuando se abre images.edit.

NO GASTA API. El truco es mandar un `size` invalido a proposito: el 403 de
verificacion salta ANTES de validar parametros, asi que la respuesta distingue
los dos estados sin generar ninguna imagen.

    403  ->  la organizacion NO esta verificada todavia
    400  ->  verificada: el modelo se acepta y solo falla la validacion

Verificar se hace a mano, es un tramite de identidad con documento oficial:
    https://platform.openai.com/settings/organization/general  ->  Verify Organization
Tarda hasta 15 minutos en propagarse.

    python tests/bodegon/verificar_25.py
"""
import io
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
os.chdir(RAIZ)

from dotenv import load_dotenv

load_dotenv()

MODELOS_25 = ["gpt-image-2.5-sunburst", "gpt-image-2.5-flare"]
REFERENCIA = "gpt-image-2"   # el que ya funciona: sirve de control


def _png_minimo() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGBA", (64, 64), (200, 0, 0, 255)).save(buf, "PNG")
    return buf.getvalue()


def estado(modelo: str, png: bytes) -> tuple:
    """Devuelve (codigo, veredicto) sin generar nada."""
    from openai import OpenAI

    cli = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    try:
        cli.images.edit(model=modelo, image=[("t.png", png, "image/png")],
                        prompt="x", size="1x1")
        return 200, "generó (inesperado: el size era inválido)"
    except Exception as e:
        cod = getattr(e, "status_code", None)
        if cod == 403:
            return 403, "organización NO verificada"
        if cod == 400:
            return 400, "disponible por images.edit"
        if cod == 404:
            return 404, "el modelo no existe para esta cuenta"
        return cod or 0, str(e)[:90]


if __name__ == "__main__":
    png = _png_minimo()
    print(f"  {'modelo':<26} {'HTTP':>5}  veredicto")
    print("  " + "-" * 66)
    listos = 0
    for m in MODELOS_25 + [REFERENCIA]:
        cod, v = estado(m, png)
        marca = "  <- control" if m == REFERENCIA else ""
        print(f"  {m:<26} {cod:>5}  {v}{marca}")
        if m in MODELOS_25 and cod == 400:
            listos += 1

    print()
    if listos == len(MODELOS_25):
        print("  ✓ los dos modelos 2.5 ya se pueden usar por images.edit.")
        print("    El cambio en el motor es la cadena de image_gen.py:105.")
    elif listos:
        print(f"  ~ {listos} de {len(MODELOS_25)} disponibles.")
    else:
        print("  ✗ todavía no. Verifica la organización a mano:")
        print("    https://platform.openai.com/settings/organization/general")
        print("    (trámite de identidad con documento; hasta 15 min en propagarse)")
    sys.exit(0 if listos == len(MODELOS_25) else 1)
