# Still by Ravel

### *Inteligencia que produce resultados.*

Plataforma de producción creativa para transformar productos, assets, datos y reglas
de marca en composiciones visuales escalables. Subís tus fotos de producto y elegís
entre tres formas distintas de generar la composición final, según cuánto control
querés tener vos vs. cuánto le delegás a la IA.

---

## 1. El flujo, de punta a punta

1. **Subís tus productos** (PNG/JPG, fondo lo que sea) — Still recorta cada foto a su
   contenido real automáticamente, sin márgenes vacíos.
2. **Elegís cómo se arma la composición** — tres modos, ver sección 2.
3. **Ajustás extras opcionales** — fondo de estudio, sombra de contacto, zoom de detalle
   (ver sección 3).
4. **Descargás** — PNG listo para usar, o PSD para seguir editando en Photoshop.

Todo corre en una sola pestaña ("Crear"), o por lotes si tenés muchos productos a la
vez (sección 4).

---

## 2. Los tres modos de generación

La diferencia central entre los tres: **qué tan libre es la IA para redibujar la
composición**, y qué tanto se garantiza que tus fotos reales queden intactas.

| | Quién decide qué | La IA toca los píxeles del producto | Velocidad |
|---|---|---|---|
| **Estilos de layout** | Vos elegís una fórmula fija (Auto, Profundidad, Impacto, etc.) | Nunca | Instantáneo |
| **Propuestas IA** | La IA analiza fotos y sugiere orden, tamaño real, héroe, posición/ángulo | Nunca — solo decide *números*, el motor determinista compone tus fotos reales | Unos segundos |
| **Generación IA** | La IA redibuja la escena entera de cero (`gpt-image-2`) | Sí — repinta la imagen completa a partir de tus fotos como referencia | 1–4 minutos |

### Estilos de layout (determinista, sin IA)
18 fórmulas geométricas fijas (Auto, Por área, Profundidad, Impacto, Radial, Cascada,
etc. — ver tabla completa en la sección 6). Cada una calcula posición/tamaño/ángulo con
una regla matemática, sin llamar a ningún modelo — gratis e instantáneo. Se muestran 3
curadas por defecto (Auto / Profundidad / Impacto) con la opción de "Ver todos los
estilos" para el resto.

### Propuestas IA (visión + motor determinista)
La IA (Groq / Gemini / Claude / OpenAI, configurable) mira las fotos reales de los
productos y propone: qué producto es el "héroe", qué tamaño real le corresponde a cada
uno (una botella de 1L no es del tamaño de un frasco de 30ml), y en el modo **"IA ·
Libre"**, la posición y el ángulo de cada producto directamente (3 variantes por
defecto: Dinámica / Elegante / Editorial). Ese análisis son solo *números* — el motor
que dibuja (`layout.py` + `renderer.py`) sigue componiendo tus fotos reales tal cual las
subiste, así el texto y el logo de cada etiqueta quedan siempre intactos y legibles.

También podés escribir tu propia idea de composición en texto libre ("Generar
propuesta") y la IA arma una variante siguiendo esa instrucción como único criterio.

### Generación IA (experimental — `gpt-image-2`)
Modo alternativo y deliberadamente distinto a los otros dos: acá la IA **sí redibuja**
la imagen final completa (usando tus fotos como referencia, no como recorte a componer).
Es el único modo donde el resultado no es 100% tus píxeles originales — a cambio, puede
proponer una composición mucho más orgánica (productos superpuestos, ángulos con
perspectiva real) que el motor geométrico no puede lograr. Validado con fidelidad de
texto/marca casi perfecta incluso en etiquetas inventadas nunca vistas por el modelo.
Límite: hasta 16 productos por composición (probado hasta 14 productos reales de
formas muy distintas con buen resultado; a 60 el modelo pierde el hilo del pedido por
completo). Tarda 1–4 minutos porque es una llamada de generación de imagen real, no un
análisis de texto.

---

## 3. Extras (aplican a los tres modos, salvo que se indique)

- **Recorte transparente al ras** — el PNG final no tiene ningún margen vacío
  reservado; termina exacto donde termina el contenido visible.
- **Sombra de contacto** — activable/desactivable.
- **Tamaño real por producto** — la IA respeta proporciones reales entre productos de
  tamaños muy distintos (solo en modos con IA).
- **Fondos de estudio** — galería de fondos generados por IA (con reflejo + luz de
  borde), opcional, solo en Estilos de layout / Propuestas IA.
- **Zoom de detalle** — burbuja de zoom automática sobre la zona que la IA considera más
  relevante del héroe (solo en modos con IA; no soportado en Generación IA).

---

## 4. Exportación

- **PNG** — siempre disponible, en los tres modos.
- **PSD** — para seguir editando en Photoshop:
  - *Estilos de layout / Propuestas IA*: una capa por producto + una capa de sombras
    (podés mover/ocultar cada producto por separado).
  - *Generación IA*: una sola capa — la IA redibujó la escena entera como una imagen
    única, no hay productos separados que envolver en capas.

---

## 5. Otras pantallas

- **Por lotes** — subís varias carpetas de productos (cada subcarpeta = una
  composición), elegís el modo y generás todas de una, descarga en ZIP. Pensado para
  catálogos grandes.
- **Proyectos** — galería de composiciones guardadas por cuenta. Reabrís cualquiera para
  seguir editándola o volver a descargarla.
- **Cuentas** — registro simple (usuario/contraseña, sin OAuth), cada usuario ve solo
  sus propias composiciones guardadas. El registro puede cerrarse por configuración
  (`REGISTRATION_OPEN`) para limitar el acceso a un grupo específico.

---

## 6. Estrategias de layout disponibles

| Estrategia | Lógica |
|---|---|
| Auto | Escala perceptual, verticales con boost |
| Por área | Mayor superficie primero |
| Alternado | Ritmo visual alto/ancho |
| Centrado | El más alto al centro de cada fila |
| Uniforme | Todos la misma altura |
| Dinámico | Contraste de tamaños extremo |
| Sombra | Productos anclados con sombra de contacto |
| Profundidad | Héroe al centro más grande, con solapamiento y sombra |
| Héroe XL | Producto central dominante (escala 1.5) |
| Escalonada | Tamaño decrece del centro hacia los lados (efecto abanico) |
| Impacto | Abanico de inclinaciones hacia afuera del héroe (hasta 30°) |
| Fila recta | Derechos en línea, sin héroe centrado |
| Cruce | Inclinados uno hacia el otro, convergen al centro |
| Inclinado (izq/der) | Todos al mismo ángulo fijo, configurable |
| Cascada | Misma inclinación para todos, crece en diagonal |
| Radial | Alturas/ángulos variados, irradia desde el centro |
| Flotante | Cada uno a su propia altura, sin línea de base compartida |

Cualquiera de estas puede combinarse con **✦ Propuestas IA** para que el orden y tamaño
real de los productos también los decida la IA, en vez de la fórmula fija.

---

## 7. Stack técnico

- **Backend:** FastAPI + Uvicorn, Pillow para composición de imágenes, `psd-tools` para
  exportación PSD.
- **IA de visión/layout:** Groq, Google Gemini, Anthropic Claude u OpenAI (configurable
  vía `AI_PROVIDER` en `.env`).
- **IA de generación de imagen:** OpenAI `gpt-image-2` (único proveedor con este modelo;
  requiere `OPENAI_API_KEY` para usar "Generación IA").
- **Frontend:** Vanilla JS + Tailwind CSS (compilado localmente, sin CDN), todo en
  `static/index.html`.
- **Auth:** sesiones propias por cookie, usuarios en `users.json`.
- **Despliegue:** contenedor Docker en Railway, con volumen persistente para sesiones y
  usuarios (`DATA_DIR`).

### Módulo legado
**Smart Objects** — Detecta y reemplaza SmartObjectLayers en archivos `.psd`/`.psb` sin
abrir Photoshop. El backend sigue activo; la pestaña está oculta desde el evolutivo 3.

## Inicio rápido

```bash
pip install -r requirements.txt
# Crear .env con al menos una de: GROQ_API_KEY / GEMINI_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY
uvicorn main:app --port 8000
```

O doble clic en `start.bat` (Windows).

---

## Documentación más técnica

- [`docs/GENERADOR_BODEGON.md`](docs/GENERADOR_BODEGON.md) — referencia funcional del
  motor de layouts (⚠️ del 2026-08-26, algunos números como el conteo de estrategias
  quedaron desactualizados respecto a este README).
- [`docs/COMO_FUNCIONAN_LAS_PROPUESTAS.md`](docs/COMO_FUNCIONAN_LAS_PROPUESTAS.md) —
  referencia técnica con código de cómo se generan las Propuestas IA.
- [`docs/ARQUITECTURA_Y_SEGURIDAD.md`](docs/ARQUITECTURA_Y_SEGURIDAD.md) — stack y
  seguridad.
- [`docs/ROADMAP_PLATAFORMA.md`](docs/ROADMAP_PLATAFORMA.md) — roadmap.
