# Rama `nuevoenfoquepromos` — estado y contexto

Documento de traspaso. Explica **qué se construyó, por qué, qué está probado y
qué falta**, para que se pueda retomar sin la conversación que lo originó.

Fuente de verdad del dominio: **"Tipos de Mecánicas promocionales" (guía de
OXXO)**, que el usuario compartió. Ese documento define la taxonomía y las
estructuras; casi todo lo de acá es una transcripción suya, no una invención.

---

## 1. El problema que se estaba resolviendo

El módulo de Promos funciona, pero cada variante de promoción está **escrita a
mano** en `static/js/promo.js`: ~1266 líneas repartidas en 8 funciones (4
variantes × 2 orientaciones). Agregar una mecánica nueva era escribir otro par
de funciones. El propio `promo.js` ya anotaba que la generalización pendiente
era **declarar el orden en vez de codificarlo**.

La pregunta original del usuario fue si se podía subir una imagen de una promo
y que la herramienta la entendiera. La respuesta a la que se llegó, con
evidencia:

- Para las **plantillas de diseño** (los PSD), la imagen es el peor camino: los
  30 PSD del usuario ya traen coordenadas exactas, nombres de capa que dicen
  qué campo es cada uno, tipografía y fondo limpio. Una imagen plana no puede
  devolver el archivo de la fuente.
- Para las **mecánicas de promoción**, la visión tampoco hacía falta: **la guía
  ya define cada mecánica como una secuencia ordenada de bloques**. La
  declaración es una transcripción del documento.

---

## 2. Lo que se construyó

### El vocabulario

Leyendo los flujos existentes salió el hallazgo que sostiene todo lo demás:
**toda promo se arma con seis tipos de bloque**, y una mecánica es un ORDEN de
esos seis.

| bloque | qué es |
|---|---|
| `asset` | imagen que NOMBRA el dato (CALLOUT, BRAND, PLECA) |
| `line` | una línea de texto ajustada al ancho (FRASE APOYO) |
| `paragraph` | párrafo de N líneas máximo (DESCRIPCION) |
| `price` | bloque de precio o porcentaje (PESOS) |
| `literal` | texto FIJO de la mecánica ("COMPRA", "GRATIS", "A SÓLO") |
| `banner` | imagen fija del motor (LLÉVATE, Y LLÉVATE) |

### Archivos

| archivo | qué hace |
|---|---|
| `static/js/promo_flow.js` | **nuevo**. El intérprete: bloques, mecánicas declaradas (vertical y horizontal) |
| `static/js/promo.js` | enganches mínimos: `ctxFlujo()`, `ctxColumnas()`, despacho, `avisarSiContradice()` |
| `promo_excel.py` | detección de alcohol, familia de mecánica, tabla de frases de la guía |
| `promo_fields.py` | emite los campos sintéticos: `PROMO_ALCOHOL`, `PROMO_FAMILIA`, `PROMO_MECANICA`, `PROMO_MECANICA_TEXTO` |
| `promo_source_retail.py` | deja pasar `CATEGORIA`, `TIPO_MECANICA`, `DESCRIPCION_MECANICA` (no se dibujan; alimentan las decisiones) |

### Convivencia, no reemplazo

El intérprete **convive** con los flujos escritos a mano detrás de
`window.PROMO_FLOW_DECLARADO`, **apagada por defecto**. Mientras no esté
probado que produce exactamente lo mismo, manda el original. Esa bandera es lo
que permite generar por los dos caminos y compararlos.

---

## 3. Cómo se decide qué mecánica dibujar

Tres fuentes, de más específica a más general. Cada una se usa **solo donde
aporta algo**:

1. **La frase redactada en la descripción** (`PROMO_MECANICA_TEXTO`). La guía
   define cada mecánica como plantilla de texto ("llévate Gratis", "y llévate
   el segundo con N%"), así que sus frases son las reglas. Gana sobre todo lo
   demás porque es la más específica: `TIPO_MECANICA` dice "Promopack" y no
   distingue sus cuatro formas.
2. **La familia declarada** (`TIPO_MECANICA` → `PROMO_FAMILIA`). Se usa en un
   solo caso: *comunicación de imagen + precio → comunicación de precio
   regular*. En las demás familias devuelve `None` a propósito (ver
   `mecanica_para_familia()`, que explica cada porqué).
3. **Las señales del dato** (hay precio, hay descuento, hay segunda
   descripción). Es la deducción que ya existía y sigue decidiendo el 79% de
   los casos reales.

Cuando la frase y la familia se **contradicen**, queda avisado en el log: casi
siempre es dato mal cargado, y antes pasaba en silencio.

---

## 4. Lo que está probado, con números

| qué | resultado |
|---|---|
| Equivalencia declarativo vs escrito a mano | **128/128 SVG idénticos** |
| Filas que ejercitan de verdad el intérprete | **98 de 128** |
| Filas iguales por definición (a mano en ambos caminos) | 30 |
| Regresión del módulo de Excel | **128/128 filas OK** |
| Tests unitarios | 10 en verde |
| Frase de la guía vs `TIPO_MECANICA`, 266 promos reales | 149 confirman, **0 contradicen**, 117 sin frase |
| Detección de alcohol | 17 categorías + trampas, 0 fallos; 88/88 promos de cerveza marcadas |

**La red de seguridad está validada en las dos orientaciones.** Se rompió a
propósito la declaración —quitando el bloque de precio— y la prueba pasó a
84/128 con 44 diferencias, tanto del lado vertical como del horizontal.

> Esto importa más de lo que parece: sin esa comprobación, un verde no
> distingue "reproduce bien" de "nunca se ejecutó". Pasó de verdad: durante un
> tiempo se contaron 100 filas como cobertura cuando eran 50, porque las 64
> horizontales iban por código a mano en ambos caminos.

### Cómo correr las pruebas

Las de navegador viven en `tests/navegador/` y necesitan el servidor levantado:

```bash
source .venv/bin/activate
python -m uvicorn main:app --port 8010 &
python tests/navegador/equivalencia_declarativa.py   # la que decide todo
python tests/navegador/regresion_excel_128.py        # regresión del módulo original
python tests/navegador/mecanicas_nuevas.py           # mecánicas nuevas + regla de alcohol
python tests/navegador/mecanica_desde_texto.py       # la frase eligiendo la mecánica
python tests/navegador/tipo_mecanica.py              # familia declarada
python tests/navegador/pleca.py                      # pleca arriba/abajo
python -m pytest tests/ -q                           # unitarias (no necesitan servidor)
```

Usuario de prueba: `testgen_still_ai` / `testpass123`.

---

## 5. Decisiones no obvias (el porqué, que es lo que se pierde)

**La pleca se dibuja como paso APARTE, no dentro del flujo.** El PSD no trae
capa para ella, así que su lugar lo elige el usuario (arriba/abajo, guardado en
el kit). Al dibujarse al final, anclada al contenido ya medido, funciona igual
en las dos orientaciones y en las cuatro variantes especiales sin tocar
ninguna. Cuando el contenido está pegado al borde se corre en bloque con un
`<g transform>`, porque si no la banda caería en coordenada negativa y el
recorte final la cortaría.

**El horizontal obligó a partir cada bloque en `medir()` y `dibujar()`.** No es
preferencia de diseño: la vertical apila (el cursor solo baja, medir y dibujar
es un paso), pero la horizontal centra cada columna verticalmente, así que hay
que saber el alto del grupo ANTES de dibujarlo.

**La detección de alcohol busca palabras COMPLETAS, no subcadenas.** "ron" vive
dentro de "macarrones": buscar sin límites de palabra marcaba como alcohol una
promo de pastas.

**Los patrones genéricos para detectar mecánica no sirven.** Se probó "empieza
con número y dice 'por $'" y clasificó mal 15 de 88 promos reales: "12 Latas
Bud Light por $146" no es una múltiple sino un pack de 12 a un precio, o sea
una simple. Un número al principio es parte de la descripción del producto.
Solo se usan frases textuales de la guía.

**"Simple: 1x / Multiple: 2x 3x 4x…"** (guía, página 1). El multiplicador de
UNO es simple. Sin esa distinción, seis promos declaradas simples ("1 x $191
Tecate…") se clasificaban como múltiples.

**Los nombres de los banners fijos NO van en el código.** Se resuelven por
clave: plantilla → kit → valor por defecto. Cambiar el banner de un cliente es
declararlo en su kit.

**Si el dato nombra una imagen que no existe, no se dibuja nada.** Antes se
estampaba una imagen de error ("FALTA ELEMENTO") sobre la pieza. Se resuelve
ANTES de armar, no al dibujar, porque en horizontal el layout reparte columnas
según haya o no callout: descartarla al dibujar dejaría una columna vacía.

---

## 6. Qué falta

**Declarar `segundo_por` y `doble_descripcion`** (30 filas del fixture). Son
las dos últimas que van escritas a mano. Tienen composición propia —textos
fijos, banners y dos casos internos cada una— no solo orden. Hasta que estén
declaradas no se puede apagar el código a mano.

**Mecánicas de la guía que no existen todavía:**
- Promopack "suma de artículos" y "compra = gratis" están **declaradas** pero
  solo la segunda se elige sola (por la frase). La primera necesita distinguirse
  de "artículo más precio", y la guía dice que la diferencia es el texto de
  apoyo inicial ("Compra") — falta implementar esa discriminación.
- **Cíclicas.** Es composición del TEXTO de la mecánica (el orden de los días,
  la "y" antes del último), no del dibujo. Va en la capa de datos.

**Falta dato real para verificar.** Los archivos del usuario ejercitan muy poca
guía: 5 combinaciones de hechos en 88 promos, todas Simple/Múltiple a precio.
Para las formas de promopack hay declaración pero no hay con qué contrastarla.
**Pedir un reporte que traiga promopack de verdad.**

**El Excel `Reporte de Promociones Reaccion Jul28.xlsx` no genera promos.** Sus
columnas son las crudas del reporte (`PRODUCTO_DESC`, `DESCRIPCION_POS`…) y
solo el perfil retail las traduce, pero ese perfil **hoy solo reconoce JSON**.
Limitación previa, no de estos cambios. Trabajo acotado si se quiere.

**La pleca no tiene hueco propio en las plantillas.** Los `template.json` solo
exponen CALLOUT y BRAND como imágenes.

---

## 7. Estado de despliegue

**Nada de esta rama está en producción.** En producción corre lo de `df7180b`.
`master` tiene el módulo de Promos completo pero tampoco está desplegado.

El despliegue a producción **requiere confirmación explícita del usuario cada
vez**.

---

## 8. Commits de la rama

```
323c8d5  La mecánica se arma leyendo la frase que la nombra en la descripción
fa81ecc  "Compra múltiple" no se reconocía como familia múltiple
e2eefea  Horizontal declarativo: columnas centradas, mismo vocabulario de bloques
3e78cdf  TIPO_MECANICA elige la mecánica donde las señales no alcanzan
07b878f  Múltiple por cantidad: "3x2" dejaba de serlo y salía como "$32"
45cc178  Regla de alcohol y tres mecánicas nuevas de la guía, sin escribir layout
5ef005e  Motor declarativo de mecánicas: primer hito, probado contra el original
```

Cada mensaje explica el porqué de su cambio, no solo el qué. Vale la pena
leerlos: `git log master..nuevoenfoquepromos`.
