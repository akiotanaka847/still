# Pruebas de navegador

El motor de promos **renderiza en el navegador** (SVG + `foreignObject` →
canvas), y eso es deliberado: la negrilla, el tachado y el superíndice por
palabra son nativos ahí. Por eso estas pruebas manejan un Chromium real con
Playwright en vez de ser tests unitarios.

Nacieron en un directorio temporal de sesión. Se trajeron acá porque son la
red de seguridad del motor declarativo: sin ellas nadie puede verificar que
sigue produciendo lo mismo que el código escrito a mano.

## Cómo correrlas

```bash
source .venv/bin/activate
python -m uvicorn main:app --port 8010 &
python tests/navegador/equivalencia_declarativa.py
```

Todas asumen el servidor en `http://127.0.0.1:8010` y el usuario de prueba
`testgen_still_ai` / `testpass123`. Las capturas van a un directorio temporal
del sistema, no al repo.

## Qué prueba cada una

| archivo | qué verifica |
|---|---|
| `equivalencia_declarativa.py` | **La que decide todo.** Genera las 128 filas del fixture por el camino escrito a mano Y por el declarado, y las compara carácter por carácter. Informa además cuántas filas tomaron REALMENTE el camino declarativo, que no es lo mismo que cuántas salieron iguales. |
| `regresion_excel_128.py` | El módulo original de Excel sigue generando 128/128 sin errores. Verifica también que una imagen inexistente se omita en vez de dibujar un error. |
| `mecanicas_nuevas.py` | Las mecánicas nuevas de la guía dibujan lo que la guía dice, y la regla de alcohol se cumple (sin "Compra", "Llévate" ni "Gratis"). |
| `mecanica_desde_texto.py` | La frase redactada en la descripción elige sola la mecánica. |
| `tipo_mecanica.py` | La familia declarada elige donde las señales no alcanzan. |
| `pleca.py` | La pleca se aplica si se subió, va donde el usuario elige (arriba/abajo) y no sale si no se subió. Sube un asset de prueba y **lo borra por API** al terminar. |

## Dos advertencias que costaron caro

**Una prueba verde no prueba nada si el camino no se ejecutó.** Durante un
tiempo `equivalencia_declarativa.py` decía 128/128 y se contaban 100 filas como
cobertura, cuando eran 50: las 64 horizontales iban por código escrito a mano
en AMBOS caminos, así que salían iguales por definición. Por eso la prueba
ahora informa las dos cifras por separado. **Antes de confiar en un verde,
rompé la declaración a propósito y confirmá que se pone en rojo.**

**No usar diálogos de confirmación del navegador en pruebas que tocan archivos
reales.** Una prueba que aceptaba `confirm()` automáticamente borró 7 SVG del
repositorio. `pleca.py` borra sus assets por API, a propósito.
