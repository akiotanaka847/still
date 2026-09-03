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
cantidad, en vez de tener un tope. En el camino determinista **sí**, y por una
razón que no aplica a la generativa: los productos son **fotos reales pegadas**,
no redibujadas, así que reducirlas pierde tamaño pero **no nitidez**.

Al medirlo aparecieron dos cosas rotas:

**Las filas estaban fijas en 3** a partir de 16 productos, y `aspect_w`/
`aspect_h` se asignaban pero **nunca se leían** — parámetros muertos. Como el
canvas es exacto al contenido, 3 filas de N crecen solo a lo ancho: 60
productos daban `12510x2975`, una tira de 4.2:1.

**`MAX_CANVAS_DIM` no era un tope.** Decía ser "tope duro del lado mayor del
canvas final", pero `_compute_export_scale` terminaba en `max(1.0, scale)`, así
que solo limitaba hacia ARRIBA: con un lienzo lógico ya mayor a 4000 px
calculaba la reducción correcta y acto seguido la descartaba. Un bodegón de 60
productos salía a 38 MP.

### Resultado

Las filas ahora se eligen para **maximizar el tamaño del producto** dentro del
tope: con el lado mayor topado, el producto sale más grande cuando el lienzo se
acerca al cuadrado. El piso de aspecto 1.0 evita que la pieza salga más alta
que ancha.

| n | filas antes → ahora | salida | aspecto | alto/producto | ganancia |
|---|---|---|---|---|---|
| 6 | 1 → 1 | 4000×1254 | 3.19 | 1078 px | igual |
| 14 | 2 → 2 | 4000×2089 | 1.91 | 900 px | igual |
| 16 | 3 → 3 | 4000×3057 | 1.31 | 869 px | igual |
| 20 | 3 → 3 | 4000×2864 | 1.40 | 815 px | igual |
| 30 | 3 → **4** | 3999×3299 | 1.21 | 701 px | **+32%** |
| 40 | 3 → **5** | 4000×3395 | 1.18 | 564 px | **+58%** |
| 60 | 3 → **6** | 4000×3546 | 1.13 | 503 px | **+87%** |

Todo `n ≤ 20` conserva el reparto que ya tenía: la mejora es solo donde antes
degradaba. Verificado renderizando 60 productos reales (PNG 14.2 MP y PSD de 61
capas, ambos 4000×3546) y sobre las 30 estrategias × 7 cantidades = 210
combinaciones.

De paso, `aspect_w`/`aspect_h` dejaron de ser código muerto: el control que el
frontend ya enviaba ahora decide el reparto (5:1 → 3 filas, 1:1 → 6 filas).

`tests/test_escala_salida.py` lo blinda en la suite automática.
