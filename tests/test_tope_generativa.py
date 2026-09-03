"""El tope de Generación IA debe ser el mismo en el servidor y en la UI.

Está duplicado a propósito —el servidor es la autoridad y responde 400, la UI
solo evita que el usuario espere 4 minutos y gaste una generación para
enterarse— pero un duplicado sin prueba se desincroniza solo. Ya pasó con
GROQ_MAX_IMAGES: quedó en 5 cuando el modelo pasó a aceptar 3, y Groq estuvo
roto para todo bodegón de 4+ productos sin que nada avisara.
"""
import re
from pathlib import Path

import image_gen

RAIZ = Path(__file__).resolve().parent.parent


def test_el_tope_de_la_ui_coincide_con_el_del_servidor():
    html = (RAIZ / "static" / "index.html").read_text(encoding="utf-8")
    m = re.search(r"const AI_GEN_MAX = (\d+)", html)
    assert m, "no se encontró AI_GEN_MAX en static/index.html"
    assert int(m.group(1)) == image_gen.MAX_PRODUCTS_FOR_GENERATION


def test_el_tope_es_el_verificado():
    """52 es el máximo probado bueno con los ajustes reales (1536x1024, alta).

    A 60 el modelo colapsa de forma REPRODUCIBLE —2 muestras de 2, ambas
    devolvieron una foto de stock sin un solo producto— así que el acantilado
    existe y está entre 53 y 59. Subir de 52 exige medir ese tramo, y con
    --alta: en calidad baja el resultado es pesimista y ya hizo fijar el tope
    en 20 cuando podía ser 52."""
    assert image_gen.MAX_PRODUCTS_FOR_GENERATION == 52
