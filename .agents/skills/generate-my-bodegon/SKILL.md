---
name: generate-my-bodegon
description: "Genera un bodegón (still life) de producto a partir de un grupo de fotos. Analizo cada producto con visión para decidir el orden óptimo, cuál es el héroe, y el tamaño real relativo de cada uno (una botella de 1L pesa distinto que un sérum de 30ml). Luego invoco el motor geométrico determinista de StillAI, que recorta cada producto al ras, lo escala, lo posiciona, añade sombras de contacto y profundidad, y entrega un PNG listo para publicar. Yo solo decido orden y tamaños — el motor compone, siempre igual. Estrategias: ai (equilibrado), ai_depth (héroe al frente), ai_shadow (anclado con sombra)."
version: 1
category: Creative
featured: yes
image: studio
integrations: [googledrive, dropbox, slack]
---

# Generar mi bodegón

Un grupo de fotos de producto → una composición PNG profesional. Yo aporto el criterio visual
(orden + tamaños reales); el motor de StillAI aporta la composición determinista. La frontera es
sagrada: **yo decido, el motor compone.**

## Cuándo usar

- "arma un bodegón con estos productos" / "compón estas 6 fotos".
- "hazme el still life de la línea X" / "necesito la foto de grupo de estos SKUs".
- Llamada por `batch-my-bodegones` una vez por grupo.

## Conexiones que necesito

Corro el trabajo externo vía Composio. Antes de empezar verifico que estén enlazadas; si falta
una, nombro la categoría, pido conectarla desde Integrations, y me detengo.

- **Google Drive / Dropbox** (almacenamiento) — de dónde leo las fotos de producto y dónde puedo
  dejar el PNG. Opcional si el usuario las suelta directo en la pestaña Files.
- **Slack** (mensajería) — opcional, solo si el usuario pide que entregue el resultado ahí
  (requiere permiso explícito antes de publicar).

## Información que necesito

Leo `config/still-config.json` primero. Por cada campo requerido que falte, hago UNA pregunta
clara y espero:

- **Las fotos de los productos** — Requerido. Por qué: son la materia prima. Si faltan pregunto:
  "¿Dónde están las fotos? Súbelas a Files o dime la carpeta de Drive/Dropbox."
- **Fondo o plantilla (opcional)** — Por qué: se escala al canvas completo detrás de los
  productos. Si no hay, el fondo queda transparente.
- **Estrategia (opcional)** — Por qué: define el tratamiento visual. Si no la dan, uso
  `ai_depth` (héroe al frente con profundidad), que es el default de la config.

## Pasos

1. **Reúne las imágenes.** Junta las fotos del grupo (Files del agente, o descárgalas de
   Drive/Dropbox vía Composio a una carpeta de trabajo). Formatos válidos: png, jpg, jpeg, webp,
   tif, bmp, gif. Nombre de archivo → nombre del producto (en mayúsculas, espacios a `_`).

2. **Asegura el motor.** Lee `config/still-config.json`. Si el repo del motor StillAI no está
   disponible localmente, clónalo desde `engine.repo`. Levanta el sidecar:
   `uvicorn main:app --port 8000` (espera a que `GET /api/status` responda `{ok:true}`).
   Nota: el backend exige sesión iniciada para las rutas `/api/bodegon/*` — si aplica, registra/
   inicia sesión primero, o corre el motor en modo local de confianza.

3. **Sube las imágenes.** `POST /api/bodegon/upload` (multipart: `products[]`, opcional
   `background`). Guarda el `session_id` y las dimensiones `{name, width, height}` que devuelve.

4. **DECIDE orden + tamaños (tu trabajo de visión).** Mira las imágenes y produce EXACTAMENTE
   este JSON, sin texto adicional:
   ```json
   {
     "order": ["NOMBRE_1", "NOMBRE_2", "..."],
     "hero": "NOMBRE_DEL_MAS_PROMINENTE",
     "sizes": {"NOMBRE_1": 1.0, "NOMBRE_2": 0.6, "...": 0.0},
     "reasoning": "1-2 oraciones en español: por qué ese orden y esos tamaños"
   }
   ```
   Criterios: héroe icónico al centro/frente; equilibrio de color (evita agrupar tonos iguales);
   contraste de formas (alterna verticales y horizontales); peso visual distribuido.
   **Tamaño real (crítico):** estima el tamaño FÍSICO real del producto (no el del recorte).
   `1.0` = el más grande del conjunto; los demás en proporción; mínimo `0.3`.

5. **Aplica tu propuesta al motor.** Como ya decidiste, NO llamas `/ai-proposal` (eso gastaría
   una API key externa). En su lugar persiste el orden+tamaños en el flujo de cómputo:
   ordena las imágenes según tu `order` y adjunta `scale` = `sizes[name]` a cada una, luego
   `POST /api/bodegon/compute` con la estrategia elegida (`ai`, `ai_depth`, `ai_shadow` para
   respetar tu orden plano/profundidad/sombra).

6. **Renderiza.** `POST /api/bodegon/render` con el mismo `session_id` y `config`. El motor
   recorta cada producto al contenido real, escala, posiciona, dibuja sombras de contacto y
   compone el PNG.

7. **Descarga y entrega.** `GET /api/bodegon/download/{session_id}` → guarda el PNG en
   `outputs/{nombre-grupo}/bodegon.png`. Si el usuario pidió Drive/Slack (con permiso), súbelo.

8. **Actualiza índices.**
   - `outputs.json` — añade `{type: "bodegon", title, summary: reasoning, path, status: "draft"}`.
   - `run-index.json` — registra `{group, session_id, strategy, count, at}`.

9. **Resume al usuario.** Dos líneas: la estrategia usada, el héroe elegido, tu `reasoning`, y la
   ruta del PNG. Ofrece variantes (`ai`, `ai_depth`, `ai_shadow`) si quiere comparar.

## Casos extremo

- **>5 productos y proveedor Groq:** Groq limita la visión a ~5 imágenes. Como aquí la visión la
  haces TÚ, no aplica — pero si el usuario forzó Groq vía API key, avísale que con >5 productos
  el orden se degrada y sugiere otra estrategia.
- **Producto sin recorte limpio (fondo opaco):** el motor recorta por color de esquinas; si el
  fondo no es uniforme, avisa que conviene una foto con fondo transparente o limpio.
- **Canvas gigante (>30000px):** el motor lo limita; si pasa, reduce `base_height`.

## Outputs

- `outputs/{nombre-grupo}/bodegon.png` — la composición final.
- `outputs.json` — una fila `type: "bodegon"`, status `draft`.
- `run-index.json` — registro de la corrida.
