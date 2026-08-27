# StillAI — Generador de Bodegón: cómo funciona todo

> Referencia funcional del módulo principal (Bodegón Generator): las 30 estrategias de
> layout, los 4 proveedores de IA, la galería de fondos de estudio y el zoom de detalle.
> Complementa a `ARQUITECTURA_Y_SEGURIDAD.md` (ese doc cubre stack/seguridad; este cubre
> **qué hace cada pieza y cómo se relacionan**).
>
> **Versión:** v2 · **Fecha:** 2026-08-26

---

## 1. El flujo, de punta a punta

```
Subís productos (PNG/JPG/...)
        │
        ▼
Se recorta cada uno a su contenido real (sin márgenes vacíos)
        │
        ├─► Estrategia GEOMÉTRICA (elegís vos)  ──────────────┐
        │                                                     │
        └─► Estrategia ✦ IA (la IA analiza y propone)         │
              orden · tamaño real · héroe                     │
              · zona de zoom · (opcional) posición/ángulo      │
                                                                ▼
                                          Motor de layout (posiciones, x/y/rotación)
                                                                │
                                                                ▼
                                    Fondo (transparente, o uno de la galería IA)
                                                                │
                                                                ▼
                                     Se componen las FOTOS REALES ahí (nunca se
                                     genera ni se toca el producto en sí)
                                                                │
                                                                ▼
                                                        PNG final
```

La idea central que atraviesa todo el módulo: **la IA nunca genera ni altera un producto**.
Solo decide *números* — orden, tamaño, posición, ángulo, qué zona ampliar — y el motor
determinista (`layout.py` + `renderer.py`) los traduce a píxeles usando tus fotos reales
tal cual las subiste. Así el texto y el logo de la etiqueta quedan siempre intactos y
legibles, sin importar cuánto se "anime" la composición.

---

## 2. Las 30 estrategias de layout

Cada una tiene versión **geométrica** (fórmula fija, instantánea, gratis) y la mayoría
también versión **✦ IA** (mismo layout, pero el orden/tamaño de los productos lo decide
el análisis de visión en vez de una regla fija por altura).

| Familia | Estrategias | Idea |
|---|---|---|
| **Clásicas** | Auto, Por área, Alternado, Centrado, Uniforme, Dinámico, Sombra | Filas balanceadas, distinto criterio de orden/escala. Las originales del producto. |
| **Profundidad** | Profundidad, Héroe XL, Escalonada | Héroe centrado más grande, solapamiento y sombra fuerte; varían cuánto crece el héroe y cómo decrece hacia los lados. |
| **Impacto** | Impacto | Abanico: el héroe queda recto al centro, los productos se inclinan hacia **afuera** (más fuerte cuanto más lejos), hasta 30°. |
| **Cruce** | Cruce | Inclinados **hacia el centro** de la fila (convergen entre sí) — funciona incluso con solo 2 productos, sin necesidad de un "héroe recto" de referencia. |
| **Inclinado parejo** | Inclinado derecha/izquierda (30°), Inclinado derecha/izquierda 45° | **Todos** los productos al mismo ángulo fijo, sin fórmula por distancia — línea prolija pero pareja, en dos direcciones y dos intensidades. |
| **Fila recta** | Fila recta | Derechos (sin inclinación), en línea, sin héroe centrado — como una foto de producto convencional. |
| **Cascada** | Cascada | Misma inclinación para todos (una sola dirección), creciendo en tamaño hacia una esquina — transmite flujo/movimiento. |
| **Radial** | Radial | Alturas y ángulos variados por posición, como si irradiaran desde un punto — para grupos de 3+. |
| **Flotante** | Flotante | Cada producto a su propia altura (sin línea de base compartida); la sombra se achica/desvanece según qué tan "lejos" flota cada uno. |
| **Libre (solo ✦ IA)** | IA · Libre | La composición completa (posición X/Y y ángulo de cada producto) la propone directamente la IA, actuando como director de arte — no una fórmula geométrica nuestra. |

`IA · Libre` es la única que no tiene versión geométrica: por definición, la posición la
inventa la IA, no hay una regla fija equivalente.

---

## 3. Qué decide la IA, exactamente

Un solo análisis de visión (una llamada, una imagen por producto) devuelve todo esto de
una vez, y se reusa para generar **las 11 propuestas ✦ IA** sin volver a llamar a la IA:

| Campo | Para qué se usa |
|---|---|
| `order` | Orden de los productos en la fila/composición. |
| `hero` | Cuál es el producto más prominente (afecta centrado, zoom de detalle). |
| `sizes` | Tamaño relativo **real** (no el de la foto) — una botella de 1 L pesa más que una lata de 250 ml aunque el recorte mida parecido. |
| `detail_crop` | Región del héroe con contenido interesante (logo, sello, texto) para la burbuja de **Zoom de detalle** — ver §5. |
| `free_layout` | Posición (cx, cy) y rotación de cada producto, **solo se usa en "IA · Libre"** — ver §2. |
| `reasoning` | 1-2 oraciones explicando el criterio (se muestra en la UI). |

El prompt le pide explícitamente pensar como director de arte: equilibrio de color,
contraste de formas, tamaño real del producto, composición con triángulos visuales y
ritmo — no solo "ordenar de mayor a menor".

### Proveedores soportados

Configurables en `.env` (`AI_PROVIDER=groq|gemini|claude|openai`, se usa el primero con
key configurada si no se fija uno):

| Proveedor | Modelo | Notas |
|---|---|---|
| **Groq** | `qwen/qwen3.6-27b` | Rápido y gratis, pero con **límite diario de tokens** en cuenta free (200k/día) — se agota fácil hacienda muchas pruebas seguidas. Requiere `reasoning_effort="none"` (si no, el modo "pensamiento" del modelo agota el budget de tokens y la respuesta sale vacía). |
| **Gemini** | `gemini-2.5-flash` | — |
| **Claude** | `claude-sonnet-4-6` | — |
| **OpenAI** | `gpt-5.1` | Agregado en v2. Buen resultado con `free_layout` (composiciones con criterio propio, ej. "triángulo dinámico"). |

Los 4 devuelven el mismo contrato JSON — cambiar de proveedor no requiere tocar nada más.

---

## 4. Fondos de estudio (galería IA)

Un selector opcional (aparece en el paso de vista previa) con 3 fondos **pre-generados
una sola vez** con IA (`gpt-image-1`) y guardados como asset del proyecto
(`static/backgrounds/`) — no se genera un fondo nuevo por cada bodegón, así queda
instantáneo y gratis por uso:

| Estilo | Onda |
|---|---|
| Estudio oscuro | Fondo oscuro con spot de luz cálido — el clásico de foto de producto premium. |
| Eléctrico | Rayos de energía cruzando el fondo, alto impacto — pensado para bebidas energéticas / deportivo. |
| Splash | Chorros de líquido a dos colores, como si el producto los generara. |

Cuando elegís un fondo de la galería, además se activa automáticamente un tratamiento de
estudio sobre cada producto (esto **no** pasa con el fondo transparente normal, ni con un
fondo propio que subas — solo con estos 3):
- **Reflejo** espejado con fade hacia abajo (técnica clásica de product shot).
- **Luz de borde** direccional — un aro de luz cálida SOLO en el borde y de un lado (no
  parejo alrededor, que en productos claros se ve como un halo pegado tipo sticker).
- **Grading** leve de contraste/saturación, para que se sienta "iluminado", no un recorte plano.

Un fondo propio que subas (función que ya existía) sigue funcionando exactamente igual
que antes — sin estos efectos — para no cambiarle el comportamiento a nadie que ya lo usaba.

---

## 5. Zoom de detalle

Toggle opcional (checkbox, junto al selector de fondo) que agrega una burbuja circular
con un recorte ampliado de una zona del héroe — logo, sello, textura — con línea guía
hacia el punto exacto del recorte.

**De dónde sale la región a ampliar**, en orden de prioridad:
1. **Con una propuesta ✦ IA**: la región que sugirió el análisis de visión (`detail_crop`)
   — la IA literalmente mira la foto y elige dónde hay algo interesante.
2. **Sin IA** (estrategia geométrica): un **detector automático** por bordes/contraste
   (`FIND_EDGES` + `BoxBlur`, sin costo de IA) encuentra la zona con más "detalle visual"
   del producto — evita que caiga en una zona lisa sin nada que mostrar.

En ambos casos, el recorte real que se amplía **siempre es cuadrado** (mismo área
aproximada que la región sugerida, centrado en ella) — la región de entrada puede venir
con cualquier proporción (la IA a veces sugiere un recuadro ancho y bajo), y forzarla tal
cual dentro de un círculo se vería estirada/deformada.

---

## 6. Configuración (`.env`)

```bash
# IA de análisis visual (orden/tamaño/zoom) — al menos una
GROQ_API_KEY=
GEMINI_API_KEY=
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
AI_PROVIDER=openai        # groq | gemini | claude | openai

# IA de generación de imagen — SOLO se usó para pre-generar los 3 fondos de
# static/backgrounds/ una vez. No hace falta para correr la app día a día.
# OPENAI_API_KEY=
```

---

## 7. Notas de implementación (para retomar el código más adelante)

- **`layout.py`** — `compute_layout()` es el motor de filas (cubre todo salvo Cascada/
  Radial/Flotante/IA·Libre, que son funciones standalone porque no encajan en el modelo
  de "una fila con héroe centrado"). `compute_layout_any()` es el punto de entrada único:
  enruta según el id de estrategia. `DEPTH_PARAMS` controla overlap/escala/inclinación
  por estrategia; `tilt_mode` distingue *fan* (Impacto), *converge* (Cruce) y *uniform*
  (Inclinado ★). `_scale_items()` y `_rotated_bbox()` están factorizados para reusarse
  entre el motor de filas y los layouts standalone.
- **`renderer.py`** — `render_png()` sigue funcionando igual si no le pasás
  `background_style` ni `zoom_detail` (cero regresión para lo que ya existía).
  `STUDIO_BACKGROUND_STYLES` mapea id → archivo en `static/backgrounds/`. El margen de
  canvas para productos rotados se calcula con la bbox real tras rotar (no una
  estimación), así ningún ángulo — ni 45° — recorta el producto contra el borde.
- **`ai_proposal.py`** — un solo prompt (`_build_prompt`) para los 4 proveedores; cada
  uno solo cambia en cómo arma el mensaje con imágenes (`_analyze_groq/_gemini/_claude/
  _openai`). `_normalize_*` valida/acota lo que devuelve la IA antes de usarlo (nunca se
  confía en el JSON crudo del modelo).
- **`static/index.html`** — el motor de layout está **duplicado en JS**
  (`computeLayoutJS` + `computeCascadaJS`/`computeRadialJS`/`computeFlotanteJS`) para que
  las miniaturas de propuestas se vean al instante sin ida y vuelta al servidor. Si se
  toca una fórmula en `layout.py`, hay que espejarla acá — están comentadas para
  facilitar eso. Las propuestas ✦ IA, en cambio, son 100% data-driven desde la respuesta
  del servidor (agregar una variante IA nueva en `main.py` no requiere tocar el JS).
