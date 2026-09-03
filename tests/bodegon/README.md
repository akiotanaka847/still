# Pruebas de escala de Bodegón

Contestan una pregunta que antes se contestaba de memoria: **¿hasta cuántos
productos aguanta cada modo?** Medido con productos reales del catálogo que ya
está en `sessions/` (161 SKUs únicos), no con imágenes de relleno.

## Scripts

| script | qué mide | gasta API |
|---|---|---|
| `set60.py` | arma el banco de N productos variados, deduplicado por SKU | no |
| `techo_escala.py` | relleno silencioso, presupuesto de tokens, píxeles por producto | no |
| `visora_60.py` | cobertura REAL de la visora: cuántos decide la IA vs. cuántos rellena el código | **sí** |
| `generativa_60.py` | dónde está el límite de `images.edit` y con qué calidad | **sí (genera imágenes)** |

Los dos últimos exigen `--go`. Sin el flag imprimen qué harían y salen.

```bash
.venv/bin/python tests/bodegon/techo_escala.py            # gratis, empezar acá
.venv/bin/python tests/bodegon/visora_60.py 60 --go
.venv/bin/python tests/bodegon/visora_60.py 60 groq --go  # comparar proveedor
.venv/bin/python tests/bodegon/generativa_60.py 20 --go --alta
```

## Resultados (2026-09-03, proveedor openai / gpt-5.1 y gpt-image-2)

### Visora — aguanta 60 sin problema

| n | cobertura | latencia |
|---|---|---|
| 14 | 60/60 → 100% | 10,6 s |
| 30 | 30/30 → 100% | 15,0 s |
| **60** | **60/60 → 100%** | **31,9 s** |

Con `gpt-5.1` no hay techo práctico a 60. Lo que sí apareció:

- **La IA repite nombres en lotes grandes**: devolvió 62 entradas para 60
  productos. Ni `_parse_result` ni `_visual_ai_images` deduplicaban, así que el
  producto repetido **se componía dos veces**. Corregido.
- **El relleno era invisible.** Si la IA cubría 20 de 60, la propuesta salía
  "completa" sin que nada dijera que dos tercios de la dirección de arte los
  puso el código por defecto. Ahora `_parse_result` devuelve `coverage`.

### Visora con Groq — no sirve para bodegones reales

`qwen/qwen3.6-27b` acepta **3** imágenes, no 5: `GROQ_MAX_IMAGES = 5` hacía que
Groq devolviera 400 para cualquier bodegón de 4+ productos, o sea para todos.
Corregido a 3. Pero incluso corregido, el tier gratuito tope **8000 TPM** revienta
con 413 a partir de ~6 productos. Groq queda para pruebas, no para uso real.

### Generativa — 60 no es un límite de tuning, es un muro

`images.edit` **acepta 60 archivos** sin quejarse (146 s en calidad baja). El
tope de 16 en el código era propio, no de la API. El límite real es de calidad:

| n | resultado |
|---|---|
| 14 | fidelidad excelente — etiquetas nítidas, sellos "EXCESO CALORÍAS" correctos, 37% transparente |
| 20 | usable — composición buena, empiezan marcas degradadas ("LMENDRA") e inventadas al fondo |
| 30 | composición se sostiene, fidelidad no: "Modelox", "Bobelia", "ALMENDDA", "Pneds" |
| **60** | **colapso total** — devolvió una foto de cumpleaños familiar, sin un solo producto |

El colapso a 60 **no es degradación gradual**: el modelo descarta el pedido
entero. Y el código lo habría servido como bodegón — no hay ninguna validación
de que la salida tenga que ver con la entrada.

La aritmética explica el muro: a 60 productos en 1536×1024 cada uno cae en
~161 px de lado, 0,23× del área que tiene en el caso verificado de 14. Una
etiqueta necesita ~40 px de alto solo para leerse.

**Para lotes grandes no hay que subir el tope: hay que agrupar y generar por
grupo**, que es lo que ya decidió `ROADMAP_PLATAFORMA.md` ("grupos por CSV",
un bodegón por grupo).
