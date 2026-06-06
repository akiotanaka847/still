---
name: batch-my-bodegones
description: "Genera muchos bodegones de una sola pasada a partir de un lote de fotos y un mapeo de grupos. Mapeo imagen→SKU por nombre de archivo (ej. 7501234.png). Agrupación dirigida por el usuario vía CSV grupo,sku: resuelvo SKU→imagen y produzco un bodegón por grupo. Para cada grupo invoco la skill generate-my-bodegon. Al final empaqueto todos los PNG en un ZIP. Pensado para catálogos de e-commerce: cientos de SKUs en grupos."
version: 1
category: Creative
featured: no
image: studio
integrations: [googledrive, googlesheets, dropbox, slack]
---

# Generar bodegones por lote

Muchos grupos de productos → muchos bodegones, en una pasada. La columna vertebral es el mapeo
**nombre de archivo = SKU** y un **CSV `grupo,sku`** que tú (usuario) controlas. No auto-agrupo:
tú decides qué SKUs van juntos.

## Cuándo usar

- "genera los bodegones de todo el catálogo" / "procesa este lote de productos".
- "tengo un CSV con los grupos, arma un bodegón por cada uno".
- Llamada por la rutina `weekly-batch-bodegones`.

## Conexiones que necesito

- **Google Drive / Dropbox** (almacenamiento) — la carpeta del lote con las imágenes `SKU.ext`.
- **Google Sheets** (hojas) — opcional, fuente del CSV `grupo,sku` si no lo sueltan como archivo.
- **Slack** (mensajería) — opcional, para avisar al terminar (con permiso).

## Información que necesito

- **La carpeta/lote de imágenes** — Requerido. Cada archivo nombrado por su SKU (ej.
  `7501234.png`). Si falta pregunto dónde está.
- **El CSV de grupos** — Requerido. Dos columnas: `grupo,sku`. Cada `grupo` produce un bodegón
  con sus SKUs. Si falta pregunto: "¿Cómo agrupo? Pásame un CSV grupo,sku o la hoja de Sheets."
- **Estrategia global (opcional)** — default `ai_depth`. El usuario puede fijar otra para todo el
  lote.

## Pasos

1. **Indexa SKU→imagen.** Recorre la carpeta del lote; construye `config/sku-index.json` mapeando
   cada SKU (stem del nombre de archivo) a su ruta. Reporta SKUs con imagen faltante.

2. **Lee los grupos.** Parsea el CSV `grupo,sku` (de archivo o de Sheets vía Composio).
   Agrupa los SKUs por `grupo`. Valida que cada SKU del CSV tenga imagen en el índice; lista los
   que no para que el usuario decida (omitir o conseguir la foto).

3. **Genera grupo por grupo.** Para cada `grupo`: reúne las imágenes de sus SKUs y llama a la
   skill **generate-my-bodegon** (orden+tamaños por visión → motor → PNG). Deja cada salida en
   `outputs/{grupo}/bodegon.png`.

4. **Maneja el volumen.** Procesa secuencialmente y registra avance en `run-index.json` para
   poder reanudar si se interrumpe. Si el lote es grande (cientos+), avisa al usuario que conviene
   un worker/cola dedicado y, si va a producto vendido, una API key medida (ver la frontera de
   licenciamiento en CLAUDE.md) — no asumas la suscripción para volumen masivo.

5. **Empaqueta.** Cuando todos los grupos estén renderizados, arma el ZIP:
   `POST /api/batch/zip` con la lista de `{session_id, name: grupo}`; luego
   `GET /api/batch/download/{batch_id}` → guarda en `outputs/_lote/{fecha}/bodegones.zip`.

6. **Actualiza índices y resume.**
   - `outputs.json` — una fila `type: "batch-bodegones"` con conteo de grupos y ruta del ZIP.
   - Resumen al usuario: cuántos grupos generados, cuántos SKUs sin imagen, ruta del ZIP, y los 1-2
     grupos que conviene revisar a mano.

## Casos extremo

- **SKU repetido en varios grupos:** permitido — la misma imagen puede aparecer en varios
  bodegones. No es error.
- **Grupo con un solo SKU:** genera igual (bodegón de un producto); avisa que con 1 producto las
  estrategias de profundidad/orden no aportan.
- **Imagen corrupta o ilegible:** el motor la omite; regístralo y repórtalo en el resumen.

## Outputs

- `outputs/{grupo}/bodegon.png` — un PNG por grupo.
- `outputs/_lote/{fecha}/bodegones.zip` — todo el lote empaquetado.
- `config/sku-index.json` — mapeo SKU→imagen del lote.
- `outputs.json` — una fila `type: "batch-bodegones"`.
