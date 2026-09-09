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

### Generativa — el tope depende de la CALIDAD pedida, no solo de la cantidad

`images.edit` **acepta 60 archivos** sin quejarse. El tope de 16 en el código
era propio, no de la API. El límite real es de fidelidad de etiqueta, y medirlo
enseñó algo que casi llevó a fijarlo mal.

**Primera medición, a 1024x1024 `quality="low"`:** n=30 salía inservible
("Modelox", "Bobelia", "ALMENDDA", "Pneds"). Con eso se fijó el tope en 20.

**Segunda medición, con los ajustes REALES de la función** (1536x1024,
`quality="high"` — lo que usa la app):

| n | resultado |
|---|---|
| 14 | excelente. Etiquetas nítidas, sellos "EXCESO CALORÍAS/AZÚCARES" correctos |
| 20 | excelente. "Havana Club 7 AÑOS", "TRES GENERACIONES AÑEJO CRISTALINO" |
| 30 | muy bueno. Un solo error visible: "GEEGO PARMESANO Y HIERRAS" |
| 45 | bueno. "Ron ABUELO AÑEJO RESERVA ESPECIAL 33% GRATIS" |
| **52** | **bueno.** "TEQUILA HACIENDA & TEPA RESERVA CRISTALINO", "Próximo by MARQUÉS DE RISCAL RIOJA", "Jim Beam Kentucky Straight BOURBON", "OREO with Coca-Cola POPPING CANDY" ← **tope actual** |
| 60 | **colapso, reproducido 2 de 2** |

**El colapso a 60 es reproducible.** Dos muestras independientes con los ajustes
de producción, y las dos devolvieron una imagen de stock sin un solo producto:
la primera una fiesta de cumpleaños familiar, la segunda una persona en una
oficina. Transparencia 0% y 2%, cuando lo normal ronda el 25-35%. No es
degradación gradual: el modelo **descarta el pedido entero**.

Así que el acantilado existe y está **entre 53 y 59**, sin acotar más fino. 52
es el máximo verificado bueno, y ahí quedó el tope.

La lección de proceso: **medir con los ajustes de producción, no con los
baratos.** Bajar la calidad para gastar menos hizo parecer inservible un caso
que sí sirve, y estuvo a punto de dejar el tope en la mitad de lo posible.

El colapso a 60 **no es degradación gradual**: el modelo descarta el pedido
entero. Y el código lo habría servido como bodegón — no hay ninguna validación
de que la salida tenga que ver con la entrada. Ese sigue siendo un riesgo
abierto.

---

## Sunburst 2.5 — el muro de 60 desaparece (2026-09-09, rama `gpt-image-2.5`)

Medido con `sunburst_25.py`, `gpt-image-2.5-sunburst` a 1536×1024, calidad
alta, fondo transparente, productos de `set_de()`.

| n | distintos | tiempo | resultado |
|---|---|---|---|
| 45 | 42 | 90 s | compone los 45 |
| 52 | 43 | 90 s | compone los 52 |
| 60 | 55 | 91 s | compone los 60 |
| 60 (2.ª) | 55 | 95 s | compone los 60 |
| 70 | 66 | 94 s | compone los 70 |
| 80 | 80 | 109 s | compone los 80 |

**No hay acantilado en todo el rango medible.** El tope de la prueba no es del
modelo: es que el catálogo solo tiene 82 productos distintos.

Compárese con `gpt-image-2` en la tabla de arriba: a n=60 no se degrada, sino
que **abandona** — descarta el pedido y devuelve una foto de stock ajena,
opaca, sin un solo producto, reproducido 2 de 2. Sunburst compone los 60 dos
de dos, y sigue componiendo a 70 y a 80.

### Lo que sí se degrada, y cómo se mide

La degradación es gradual y se ve en los errores de marca. La forma limpia de
medirla son los SKU **ficticios** del banco (VORPLIX, QUENZORA, ZUNKO,
LUMORA): el modelo no los puede recordar, solo leerlos.

| n | los SKU ficticios | errores en marcas reales |
|---|---|---|
| 45 | correctos, códigos incluidos | pocos, en texto secundario |
| 52 | correctos | "Modello", "landatti", "MENNICK Dip" |
| 60 | correctos | 4-6: "Ravana Club", "PANTENS", "CRIVS REGAL", "Sumbites" |
| 70 | **"BERRY ROSA" en vez de "BERRY NOVA"** | más: "Affligen", "Superia", "Jack Däniel" |
| 80 | inconsistentes | muchos: "LaReonás", "Diamjande", "XOT ENERY", "Jovano Club" |

A 45 y 52 los errores están al nivel de `gpt-image-2`. **La ganancia no es
"comete menos errores": es que 60 y más deja de ser un muro.**

### Dos trampas de acceso, ambas encontradas gastando llamadas

1. Los modelos **no aparecen en `/v1/models`**. Buscar ahí da falso negativo.

2. Por `images.edit` —la vía del motor— devuelven **403: "Your organization
   must be verified"**. Por Responses API con la herramienta
   `image_generation` funcionan sin verificar, y por eso el script usa
   Responses.

3. La vía Responses impone un límite que `images.edit` no tiene: **30000
   patches por imagen**. El catálogo tiene una Fanta de 6600×10200 (67 Mpx,
   24 MB) que pide 33354 y tumba la petición ENTERA con un 400 — no degrada,
   rechaza. Hay que reducir la entrada; el script la baja a 1024 px de lado
   mayor conservando PNG con alfa.

### Un error de montaje que costó dos generaciones

No usar `products/` de una sesión como banco de prueba. La carpeta de una
sesión de 60 tiene **4 imágenes distintas repetidas once veces cada una**, así
que `products/[:45]` prueba 4 productos repetidos, no 45 — y el modelo
devuelve una rejilla ordenada que parece un fallo y no lo es. `set_de()` existe
para esto: deduplica por SKU entre todas las sesiones.

### Lo que la 2.5 abre y el motor todavía no usa (de la documentación)

Dos cosas que no se ven midiendo, solo leyendo la documentación, y que cambian
el tamaño de la oportunidad:

| | `gpt-image-2` | 2.5 |
|---|---|---|
| calidad | low, medium, high | low, medium, high, **xhigh**, **max**, auto |
| tamaño | tamaños fijos | múltiplos de 16, proporción 1:3 a 3:1, **hasta 3840 px por lado y 8.294.400 px totales** |

El motor está fijo en **1536×1024 = 1,57 Mpx**, o sea el **19% del máximo**, y
pide `high`, el tercer nivel de seis. **Toda la curva de arriba se midió con
ese ajuste conservador**, así que el techo de productos puede no ser del modelo
sino de los píxeles que le dimos para dibujar las etiquetas.

Esto ataca además la debilidad estructural del modo generativo: daba
1536×1024 mientras Layout y Visora llegan a 4000 px. A 3840×2160 deja de ser
el motor de baja resolución.

Cuidado con el coste: se cobra **por token de salida**, así que subir el lienzo
sube el precio de cada pieza casi en proporción.

**Precio, con las fuentes en desacuerdo:** los dos modelos cobran igual entre
sí ($5/1M texto entrada, $8/1M imagen entrada, $30/1M imagen salida). Si eso es
igual o el doble que `gpt-image-2` depende de la fuente que se lea. La forma de
saberlo es mirar la facturación real.

**Flare vs Sunburst:** Flare es el que OpenAI pone por defecto (rápido, 50%
menos latencia **que gpt-image-2**, no que Sunburst); Sunburst es para
precisión de edición. Medido aquí, Flare fue solo 13% más rápido que Sunburst
— no contradice el 50%, porque ese número es contra el modelo viejo. Para
imagen de producto pulida el perfil correcto es Sunburst.

### Medido: más lienzo arregla las marcas grandes, no el texto chico

n=60, mismo modelo, misma calidad `high`, **cambiando solo el lienzo**:

| lienzo | píxeles | tiempo | peso |
|---|---|---|---|
| 1536×1024 | 1,57 Mpx | 95 s | 3,0 MB |
| 3072×2048 | 6,29 Mpx | **102 s** | 11,6 MB |

**Cuatro veces los píxeles por un 7% más de tiempo.** El coste sí sube casi en
proporción, porque se cobra por token de salida.

Comparando productos cuya marca se podía leer en las dos:

| producto | 1536×1024 | 3072×2048 |
|---|---|---|
| Chivas Regal | "CAITS REGAL" | **"CHIVAS REGAL"** ✓ |
| Havana Club | "Ravana Club" / "Warong Club" | **"Havana Club 7"** ✓ |
| Bohemia | "Bohemia" | **"Bohemia CRISTAL est. 1905"** ✓ |
| Passport Scotch | "Passport Sootch" | "PASSPORT GOGTCH" ✗ sigue mal |
| VORPLIX (ficticio) | correcto | correcto ✓ |

**SESGO DE OBSERVACIÓN, importante para no engañarse:** contar errores en las
dos y compararlos NO es válido. A 1536 mucho texto chico es ilegible, y lo
ilegible no se puede juzgar mal. A 3072 se leen más cosas, así que aparecen
"más errores" que en realidad ya estaban. La comparación honesta es producto
por producto entre los que se leían en ambas.

Conclusión: la resolución arregla **la marca principal** de los productos
prominentes, y no arregla el texto secundario. Los errores que quedan a 3072
son de lectura del modelo, no de píxeles: "Superion" por Superior, "OJOE
TIGRE", "FUND·ONDR", "AOeS" por AdeS, "Jemesse Honey" por Tennessee.

Sin probar todavía: `xhigh` y `max`, que es la otra palanca y la única que
queda para el texto chico.

### Por qué hace falta verificar la organización

`images.edit` con los 2.5 devuelve 403 hasta que la organización esté
verificada. Es un trámite de identidad, gratis, operado por Persona (un
tercero) que cruza contra listas de sanciones de más de 220 países. Pide
documento oficial y **un documento solo verifica una organización cada 90
días**. No hay endpoint: `/v1/organization` da 404.

`verificar_25.py` dice si ya se activó **sin gastar una generación**, mandando
un `size` inválido a propósito: el 403 de verificación salta antes de validar
parámetros, así que 403 = no verificada y 400 = disponible.

### Lo que falta antes de decidir

- Una sola muestra en 45, 52, 70 y 80 (en 60 hay dos).
- Flare sin probar por encima de 5 productos.
- La vía `images.edit` sin probar, por el 403.
- **Si la calidad a 60-80 es entregable es un juicio de negocio, no una
  medición.** Los errores de marca existen; cuántos son tolerables lo decide
  quien entrega la pieza.

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
