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

---

## Escala del camino determinista (2026-09-03)

La pregunta era si los productos podían encoger con gracia al crecer la
cantidad. En el camino determinista **sí**, y por una razón que no aplica a la
generativa: los productos son **fotos reales pegadas**, no redibujadas.
`_process_item_image` carga el original, lo recorta y hace **un solo** resize
LANCZOS al tamaño final, así que reducir pierde tamaño pero **no nitidez**.

Medido con el catálogo real, a 60 productos se **reduce** en 57 de 60 casos
(mediana 3.3x) y los 3 que se amplían quedan en 0.89x, casi 1:1. Verificado
mirando recortes a escala 1:1: sellos "EXCESO CALORÍAS", "CONTIENE 12 BOTELLAS
355 ml" y el texto diminuto de un pañal, todos legibles.

### Tres cosas estaban rotas

**Las filas estaban fijas** (1 hasta 6, 2 hasta 15, 3 en adelante) y
`aspect_w`/`aspect_h` se asignaban pero **nunca se leían** — parámetros
muertos. Como el canvas es exacto al contenido, 3 filas de N crecen solo a lo
ancho: 60 productos daban `12510x2975`, una tira de 4.2:1.

**`MAX_CANVAS_DIM` no era un tope.** Decía ser "tope duro del lado mayor del
canvas final", pero `_compute_export_scale` terminaba en `max(1.0, scale)`:
solo limitaba HACIA ARRIBA. Con un lienzo ya mayor a 4000 px calculaba la
reducción correcta y acto seguido la descartaba. 60 productos salían a 38 MP.

**El defecto también estaba en los sets chicos**, y eso no se había visto: con
envases anchos (bolsas, six-packs) una hilera de 6 daba una tira de **9.97:1**
y el producto salía a 375 px pudiendo salir a 902.

### La regla que quedó

Una sola, para todo n: **si el reparto de siempre cabe bajo el tope, se
respeta**. Eso preserva la estética deliberada de los sets chicos (una hilera
es la composición clásica; optimizar por tamaño elegía 2 filas con 6 productos
—84% más grandes y peor pieza—) y evita mover casos que no estaban rotos.

Cuando no cabe, se elige el reparto que **maximiza el tamaño del producto**:
con el lado mayor topado, gana el que acerca el lienzo al cuadrado. Se mide con
el **empaquetado real**, no estimando: la estimación "productos por fila x
razón media" erraba porque las filas se balancean por ancho y los productos van
de 0.23 a 2.80 de razón, y por ese error se elegían repartos peores que el
anterior en sets de 16 y 25.

### Resultado, catálogo real

| n | filas | salida | aspecto | alto/producto | vs. antes |
|---|---|---|---|---|---|
| 6 | 1 | 4000×1009 | 3.96 | 886 px | igual |
| 14 | 2 | 4000×2497 | 1.60 | 1104 px | igual |
| 20 | 3 | 3874×4000 | 0.97 | 1184 px | igual |
| 25 | 3 | 4000×3116 | 1.28 | 903 px | igual |
| 30 | **4** | 4000×3855 | 1.04 | 830 px | **+34%** |
| 40 | **5** | 3681×4000 | 0.92 | 688 px | **+46%** |
| 60 | **6** | 3979×3965 | 0.99 | 572 px | **+95%** |

**Peor caso: +0%.** No hay ninguna regresión. Y desapareció el escalón entre 15
y 16 productos, que con envases anchos duplicaba el tamaño (313 → 599 px)
porque recién ahí entraba el cálculo.

De paso, `aspect_w`/`aspect_h` dejaron de ser código muerto: el control que el
frontend ya enviaba ahora decide el reparto (5:1 → 3 filas, 1:1 → 6 filas).

`tests/test_escala_salida.py` lo blinda con 141 casos sobre tres mezclas de
proporciones. La primera versión usaba productos sintéticos todos de razón 0.5
y por eso **no detectó** las regresiones de 16, 20 y 25: la razón media cambia
con la mezcla y es justo lo que hace cambiar la decisión.
