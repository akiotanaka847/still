"""Config de pytest para las pruebas de Still.

Agrega la raíz del repo al path para poder importar los módulos de la app
(layout, renderer, ai_proposal, ...) sin instalarla como paquete.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def pytest_addoption(parser):
    parser.addoption(
        "--regenerate-golden",
        action="store_true",
        help="Regenera los archivos golden en vez de verificar contra ellos. "
             "Usar SOLO cuando el cambio de comportamiento es deliberado, y "
             "revisando el diff resultante a mano.",
    )
