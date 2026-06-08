# PRD: Still Studio — Plataforma de Bodegones de Producto (qaio agent + SaaS)

**Status**: Draft
**Author**: Alex (PM)  **Last Updated**: 2026-06-07  **Version**: 1.0
**Stakeholders**: Backend (motor + plataforma), AI/Agent (visión + intake), Data (DB/storage/colas), Design/UI
**Engine ground truth**: `layout.py`, `renderer.py`, `main.py` (endpoints reales con prefijo `/api/bodegon/*` y `/api/batch/*`), `ai_proposal.py`. Roadmap previo: `docs/ROADMAP_PLATAFORMA.md`.

> **Frontera sagrada (no negociable).** La IA SOLO decide `{order, hero, sizes}` (qué productos, cuál es el héroe, tamaño físico real relativo de cada uno). El motor geométrico determinista hace TODA la composición (recorte al contenido, escala, posición, filas, sombras, profundidad, PNG final). Nunca se "compone a mano". Si el resultado no gusta → se cambia estrategia u orden/tamaños, jamás se mueven píxeles.

---

## 1. Product Vision & Target Users

### Visión
Still Studio convierte fotos sueltas de producto en bodegones (still life) listos para publicar, de forma consistente y a escala de catálogo. Hoy existe como herramienta (FastAPI de un archivo + motor geométrico) y como agente qaio que auto-decide todo. Lo llevamos a: (1) un **agente qaio que entrevista al usuario antes de componer** (el usuario manda, con defaults inteligentes), y (2) una **plataforma SaaS** capaz de procesar cientos–miles de SKUs por lote.

### Target users & JTBD
| Usuario | Contexto | Job To Be Done |
|---|---|---|
| **Coordinador de e-commerce / catálogo** (primario) | Equipo de 2–10, retail/marca, 100s–1000s de SKUs, no técnico | "Cuando lanzo o actualizo una categoría, quiero generar fotos de grupo consistentes por línea sin diseñador, para publicar rápido en la tienda." |
| **Diseñador / retoque** (secundario) | Hace packshots a mano hoy | "Quiero descargar el trabajo repetitivo de composición al motor y solo revisar/aprobar, para dedicarme a lo creativo." |
| **Operador de marketplace / agencia** (expansión, gatilla SaaS/reventa) | Procesa catálogos de terceros | "Quiero generar bodegones para muchas marcas a volumen, facturado, sin tocar el subscription interno." |

### El problema central (el que dispara este PRD)
El agente actual **auto-decide todo** (fondo, sombras, resolución, estrategia) y el usuario está frustrado: pierde control sobre decisiones que sí le importan. **El agente debe correr una ENTREVISTA DE INTAKE** que pregunte lo que el usuario realmente quiere antes de componer — con defaults inteligentes, pero preguntando.

**Evidencia**: Requerimiento directo del usuario (dueño de producto). Señal de comportamiento: la config del agente fija `default_strategy: ai_depth` y la skill `generate-my-bodegon` solo confirma 3 cosas; el resto se asume. Señal de motor: `layout.py`/`renderer.py` exponen ~8 parámetros + sombras + fondo que hoy el usuario nunca elige.

---

## 2. La ENTREVISTA DE INTAKE (corazón de la v1 del agente)

### Principios
1. **Preguntar, no asumir** — pero con default inteligente visible ("uso X salvo que prefieras otra cosa").
2. **Una pregunta clara a la vez**, voz español neutro LatAm, usuario NO técnico → nada de "JSON", "param", "endpoint", "px".
3. **Progresiva**: arranca por lo que más cambia el resultado (single vs lote, fondo, sombras); las avanzadas solo si el usuario quiere afinar.
4. **Memoria de preferencias**: una vez contestadas, se guardan como default del proyecto/lote y solo se reconfirman si el usuario lo pide.
5. **GAP-aware**: si la respuesta requiere una capacidad aún no construida (ver GAPs), el agente lo dice y ofrece el fallback disponible. Nunca promete lo que el motor no hace todavía.

### Orden de la entrevista
**Bloque A — Alcance** (siempre) → **Bloque B — Look principal** (siempre; sombras, fondo, tamaños) → **Bloque C — Salida** (siempre; resolución, formato) → **Bloque D — Avanzado** (solo si el usuario pide afinar; aspect ratio, estrategia explícita, gap).

### Cuestionario completo

Leyenda de mapeo: `compute/render config.<param>` = campo de `config` que va a `POST /api/bodegon/compute` y `/render`. **GAP#** = capacidad no construida aún (ver §3 e inventario de gaps). "Agente" = lo resuelve el agente sin tocar el motor.

| # | Pregunta (voz al usuario) | Cuándo se pregunta | Opciones | Default inteligente | Mapeo → motor / endpoint / GAP |
|---|---|---|---|---|---|
| A1 | ¿Es una sola composición o un lote de muchos grupos? | Siempre, primero | Una sola · Lote por SKU | Una sola (si llegan <12 fotos sin CSV); Lote (si hay CSV `grupo,sku` o carpeta grande) | Single → skill `generate-my-bodegon` (`/api/bodegon/*`). Lote → skill `batch-my-bodegones` + `config/sku-index.json` + `/api/batch/zip`. Volumen masivo → **GAP5** (colas/workers) + frontera de licenciamiento |
| A2 | ¿Dónde están las fotos? | Siempre, si faltan | Files del agente · Drive/Dropbox (Composio) | Files del agente | Reúne imágenes → `POST /api/bodegon/upload` (`products[]`) |
| A3 (lote) | ¿Cómo agrupo los SKUs? | Solo si A1=Lote | CSV `grupo,sku` (archivo) · Google Sheets | Pedir CSV; no auto-agrupar | Parse CSV → resuelve SKU→imagen (nombre de archivo = SKU) → un grupo = un bodegón |
| B1 | ¿Quieres sombra bajo los productos? ¿Sutil o marcada? | Siempre | Sin sombra · Sutil · Marcada | **Sutil** (grounding natural) | "Marcada" → estrategia `*_shadow`/`profundidad` (sube `shadow_opacity` 0.30, `shadow_blur`, `shadow_margin` en `layout.py`). "Sutil" → cualquier estrategia (sombra base 0.16). "Sin sombra" → **GAP** menor: hoy el motor siempre dibuja grounding sutil → requiere flag `shadow:false` en `compute` (pequeña extensión de motor) |
| B2 | ¿El fondo debe ser transparente, una imagen tuya, o un estilo de escena? | Siempre | Transparente · Mi imagen · Estilo curado | **Transparente** (más versátil para e-commerce) | Transparente → no `background` en upload (default actual). Mi imagen → `background` en `POST /api/bodegon/upload` + `render` lo escala. Estilo curado → **GAP2** (librería Minimalista/Premium/Natural/Colorido/Editorial + podios) |
| B3 | ¿Estas fotos vienen con fondo recortado o con fondo de estudio? | Siempre que B2≠"Mi imagen ya compuesta"; clave si imágenes mezcladas | Ya recortadas · Con fondo (quítalo tú) · No sé | **No sé → autodetectar** | Detecta alpha; opaca → quitar fondo → trim. Hoy `renderer.py` solo recorta por alpha o color de esquina (frágil). Quitar fondo real = **GAP1** (rembg CPU → BiRefNet GPU) |
| B4 | ¿Respeto el tamaño real de cada producto (1L ≫ 30ml) o los igualo? | Siempre | Tamaño real (recomendado) · Igualar todos | **Tamaño real** | Tamaño real → el agente produce `sizes` por visión y se adjunta `scale` por item (estrategias `ai*`). Igualar → estrategia geométrica sin `sizes` o todos `scale=1.0` (`uniforme`) |
| B5 | ¿Algún producto debe ser el protagonista (héroe)? | Siempre; el agente propone | (el agente sugiere) · Yo elijo: ___ | Agente decide `hero` por visión; lo muestra para confirmar | `hero` del contrato `{order,hero,sizes}`; estrategia `ai_depth` lo pone al frente |
| C1 | ¿Para qué la vas a usar? (web, ficha de producto, banner grande…) | Siempre | Web/ficha · Banner/impresión · Personalizada | **Web/ficha → 760–1200 px** | Mapea a `compute/render config.base_height` (300–2000 hoy). Alta resolución Pro >2000 px = **GAP3** (export Pro) |
| C2 | ¿En qué formato lo entrego? | Siempre | PNG (transparencia) · JPG (liviano) · WebP | **PNG** si fondo transparente; **JPG** si fondo opaco | **GAP4**: `render`/`download` hoy solo PNG. Requiere param `output_format` en render y `download` con media_type correcto |
| D1 | ¿Proporción del lienzo? | Solo "afinar" | Auto · Cuadrado 1:1 · Horizontal · Vertical | **Auto** (el motor decide filas/ratio por nº de productos) | `compute/render config.aspect_w` / `config.aspect_h` (0/0 = auto en `layout.py`) |
| D2 | ¿Cómo quieres el arreglo? (equilibrado, con profundidad, anclado con sombra…) | Solo "afinar"; si no, derivado de B1/B4 | Equilibrado · Profundidad · Sombra · (geométricas: Centrado, Escalonada, Héroe XL…) | **`ai_depth`** (default de config) | `compute/render config.sort_strategy` ∈ las 13 de `SORT_STRATEGIES`. Respetar `order/sizes` del agente exige una estrategia `ai*` |
| D3 | ¿Más juntos o más separados? | Solo "afinar" | Juntos · Normal · Separados | **Normal (~40)** | `compute/render config.item_gap` (0–200). Nota: estrategias de profundidad usan solapamiento, no gap |
| D4 (lote) | ¿Aplico estas mismas decisiones a todo el lote? | Solo si A1=Lote, al final | Sí, a todo · Reviso por grupo | **Sí, a todo** | Estrategia/fondo/formato globales del lote; excepciones por grupo en revisión |

### Reglas de derivación (para no sobre-preguntar)
- B1=Marcada + B4=Tamaño real → si D2 no se contesta, el agente usa `ai_shadow` o `ai_depth` según B5.
- B2=Transparente → C2 default PNG y se omite la pregunta de "fondo de estudio" salvo imágenes mezcladas.
- A1=Una sola con ≤2 productos → el agente avisa que profundidad/orden aportan poco y simplifica D2.

### Contrato que el agente sigue produciendo (sin cambios)
```json
{ "order": ["P1","P2"], "hero": "P1", "sizes": {"P1":1.0,"P2":0.6}, "reasoning": "..." }
```
El agente NO llama `/api/bodegon/ai-proposal` (gastaría API key externa): hace la visión él mismo, ordena las imágenes y adjunta `scale=sizes[name]` por item, luego `compute` + `render` con la estrategia elegida en el intake.

---

## 3. Roadmap por fases (F1 / F2 / F3)

Owner por disciplina: **[BE]** backend/motor · **[AI]** agente/visión · **[DATA]** DB/storage/colas · **[UI]** UI/plataforma.

### F1 — Motor batch por SKU + Intake del agente (sobre el código actual)
**Objetivo**: que el usuario controle el resultado vía intake, que las imágenes mezcladas salgan limpias, y que el pipeline por SKU funcione end-to-end en un solo nodo. Sin auth de plataforma ni UI nueva.

**Scope concreto**
- **Intake del agente** [AI]: implementar el cuestionario §2 en `generate-my-bodegon` y `batch-my-bodegones`; persistir respuestas como defaults de proyecto/lote.
- **GAP1 — Quitar fondo** [BE]: detectar alpha en `upload`/normalización; si opaca → rembg (CPU) → trim al ras. Reemplaza el recorte por color de esquina frágil de `renderer.py` para imágenes opacas.
- **GAP4 — Formato de salida** [BE]: añadir `output_format` (png/jpg/webp) a `render`; `download` responde con `media_type` y extensión correctos.
- **Sombra "sin sombra"** [BE]: flag `shadow` en `compute` para permitir B1=Sin sombra (hoy siempre on).
- **GAP5 (mínimo) — Batch en un nodo** [DATA]: tabla `jobs` en SQLite + worker loop secuencial; índice SKU→imagen; reanudable vía `run-index.json`.
- **Normalización a escala** [BE/DATA]: rembg condicional + trim consistente sobre lote mezclado (riesgo #1 del roadmap previo — validar primero).

**Acceptance criteria**
- [ ] El agente NUNCA compone sin pasar Bloques A–C del intake; las respuestas quedan guardadas y reutilizables.
- [ ] Una imagen opaca entra y sale recortada al ras sin halos en ≥90% de un set de prueba de 50 packshots mezclados.
- [ ] `render` produce PNG, JPG y WebP; el JPG de un fondo opaco pesa <40% del PNG equivalente.
- [ ] Lote de 100 SKUs en CSV `grupo,sku` → un PNG por grupo + ZIP, reanudable si se interrumpe.
- [ ] La frontera sagrada se mantiene: cero cambios de composición fuera de `{order,hero,sizes}` + estrategia.

### F2 — Plataforma (mockup completo)
**Objetivo**: convertir el script de un nodo en SaaS multiusuario con proyectos, storage en nube, workers a escala y UI pulida (sidebar Inicio/Proyectos/Plantillas/Estilos/Ajustes).

**Scope concreto**
- **Auth + proyectos** [BE/DATA]: subir el PBKDF2 actual a usuarios/proyectos en Postgres; sesiones por proyecto.
- **Storage + DB** [DATA]: disco local → S3/Azure Blob; SQLite → Postgres; índice SKU→imagen persistente por proyecto.
- **Colas/workers a escala** [DATA]: SQLite jobs → Redis + RQ/Celery; **GAP1** rembg CPU → BiRefNet en worker GPU.
- **GAP2 — Librería de estilos curados** [BE/UI]: Minimalista, Premium, Natural, Colorido, Editorial + podios/escenas como plantillas de fondo (no IA generativa todavía); se inyectan como `background` al `render`.
- **GAP3 — Export Pro alta resolución** [BE]: `base_height` >2000 px en tier Pro, con límite de canvas (30000 px ya en `layout.py`).
- **UI** [UI]: navegar lote por grupo → aprobar → exportar ZIP; selector visual del intake (mismas preguntas §2, controles en vez de chat).

**Acceptance criteria**
- [ ] Dos usuarios trabajan proyectos aislados sin ver datos del otro.
- [ ] Un lote de 1000 SKUs se procesa con workers GPU sin tumbar el nodo; progreso visible por grupo.
- [ ] El usuario aplica un estilo curado y obtiene una escena coherente sin subir imagen propia.
- [ ] Export Pro entrega una imagen nítida apta para impresión sin re-componer (misma geometría, mayor base).

### F3 — Monetización + integraciones
**Objetivo**: cobrar por valor y conectar con el ecosistema del cliente.

**Scope concreto**
- **GAP6 — Créditos / Pro / billing** [BE/UI]: medición por bodegón/lote; tier Pro desbloquea alta resolución (GAP3), estilos premium, lotes grandes.
- **Frontera de licenciamiento** [AI/BE]: reventa/lotes masivos NO usan el subscription qaio; requieren API key medida (Groq) o worker dedicado. El sistema detecta el caso y lo exige (no asume).
- **Fondos IA opcionales** [BE]: generación de fondos sobre la librería curada (opt-in, de pago).
- **GAP7 — Conectores Shopify / PIM** [BE/DATA]: ingesta de catálogo (SKU + imagen) y publicación de resultados.

**Acceptance criteria**
- [ ] Un uso de reventa/lote masivo es bloqueado del subscription y guiado a configurar API key medida o worker.
- [ ] Pro vs Free difieren de forma medible (resolución, estilos, volumen) y el consumo de créditos es auditable.
- [ ] Un SKU de Shopify entra, se genera su bodegón y la imagen vuelve a la ficha sin pasos manuales.

### Backlog priorizado (épicas → historias)

**Épica 1 — Intake Interview [AI]** (F1, máxima prioridad)
- H1.1 Como coordinador, quiero que el agente me pregunte fondo/sombras/resolución/formato antes de componer, para no perder control. *AC: bloques A–C obligatorios; defaults visibles.*
- H1.2 Como usuario, quiero que recuerde mis respuestas por proyecto, para no repetirlas. *AC: defaults persistidos y reutilizados.*
- H1.3 Como usuario, quiero "afinar" (aspect/estrategia/gap) solo cuando lo pida. *AC: Bloque D opcional.*

**Épica 2 — Normalización / quitar fondo [BE]** (F1, riesgo #1)
- H2.1 Detectar alpha vs opaca en upload. *AC: clasificación correcta ≥98%.*
- H2.2 rembg + trim para opacas. *AC: sin halos en ≥90% del set de prueba.*

**Épica 3 — Salida flexible [BE]** (F1)
- H3.1 `output_format` png/jpg/webp en render+download. H3.2 flag `shadow` on/off.

**Épica 4 — Batch por SKU [DATA]** (F1)
- H4.1 Índice SKU→imagen desde nombres de archivo. H4.2 Jobs SQLite + worker reanudable. H4.3 ZIP por lote.

**Épica 5 — Plataforma [DATA/BE/UI]** (F2): auth/proyectos · Postgres+S3 · Redis/RQ+GPU · UI revisión por grupo.

**Épica 6 — Estilos curados [BE/UI]** (F2): 5 estilos + podios; selector visual.

**Épica 7 — Pro & resolución [BE]** (F2/F3): export alta resolución + gating.

**Épica 8 — Monetización & licenciamiento [BE/AI]** (F3): créditos/billing + detección de reventa.

**Épica 9 — Conectores [BE/DATA]** (F3): Shopify/PIM in/out.

---

## 4. Success Metrics & Non-Goals

### Success metrics
| Objetivo | Métrica | Baseline | Target | Ventana |
|---|---|---|---|---|
| Control del usuario (problema central) | % de composiciones precedidas por intake completo | 0% (auto-decide) | 100% | F1 |
| Calidad de normalización (riesgo #1) | % packshots limpios sin halo desde set mezclado | n/d | ≥90% | F1, set de 50 |
| Throughput batch | SKUs procesados por lote sin intervención | ~decenas | ≥1000 | F2 |
| Aprobación al primer intento | % grupos aprobados sin re-tirar | n/d | ≥70% | F2, 30 días |
| Consistencia (frontera sagrada) | % salidas con composición idéntica para mismo input/config | — | 100% | continuo |
| Monetización | % usuarios activos en Pro | 0% | establecer baseline → +X | F3, 90 días |

### Non-Goals (v1 / explícitos)
- **No auto-agrupar SKUs.** El usuario manda los grupos (CSV `grupo,sku`). El sistema solo resuelve SKU→imagen.
- **No componer a mano / pixel-push.** Jamás. Solo `{order,hero,sizes}` + estrategia.
- **No fondos IA generativos en F1/F2.** Primero librería curada; IA generativa es F3 opt-in.
- **No usar el subscription qaio para reventa o lotes masivos.** Eso exige API key medida o worker dedicado (frontera de licenciamiento).
- **No multi-idioma de UI en F1.** Español neutro LatAm primero (usuario no técnico).
- **No editor de estilos en F2.** Plantillas fijas; el editor es posterior.
- **No video / 3D / animación.** Fuera de alcance del producto.

---

## 5. Apéndice — Referencias de código (ground truth)
- Estrategias (13): `SORT_STRATEGIES` en `layout.py`. Profundidad: `DEPTH_PARAMS`. Sombra: `shadow_opacity/blur/margin` derivados de `strong_shadow`.
- Params de composición: `compute_layout(... sort_strategy, base_height, item_gap, max_vertical_boost, aspect_w, aspect_h, rows_count ...)`.
- `sizes`→`scale` por item controla **área visual** (no altura) en estrategias `ai*` (ver bloque "scale" en `layout.py`).
- Endpoints reales (con prefijo): `POST /api/bodegon/upload` · `/compute` · `/render` · `GET /api/bodegon/download/{id}` · `POST /api/bodegon/ai-proposal` · `POST /api/batch/zip` · `GET /api/batch/download/{id}`. Rutas `/api/bodegon/*` y `/api/batch/*` exigen sesión (`auth_guard`).
- Agente: `qaio.json` (`config/still-config.json` → `default_strategy: ai_depth`, `ai.provider: qaio`), `CLAUDE.md` (3 capas + frontera de licenciamiento), skills `generate-my-bodegon` / `batch-my-bodegones`.
