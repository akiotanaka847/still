# Still AI — Plan de plataforma (ingesta masiva por SKU)

> Estado: **diseñado, NO implementado.** Documento de referencia. Fijado 2026-06-03.

## Visión
Pasar de la herramienta actual (FastAPI de un archivo, sesiones en disco) a una
plataforma SaaS "Still AI": sidebar (Inicio / Proyectos / Plantillas / Estilos /
Ajustes), plan Pro / créditos, estilos de fondo (Minimalista, Premium, Natural,
Colorido, Editorial) y propuestas tipo fotografía real con podios/escenas.

La **columna vertebral es la ingesta masiva orientada a SKU.**

## Decisiones fijadas
| Tema | Decisión |
|---|---|
| Mapeo imagen→SKU | **Nombre de archivo = SKU** (ej. `7501234.png`). Sin dependencias. |
| Estado de imágenes | **Mezcladas**: detectar alpha; si es opaca → quitar fondo; luego trim al ras. |
| Agrupación | **Dirigida por el usuario**: pasa grupos de SKUs (CSV `grupo,sku`); el sistema resuelve SKU→imagen y genera un bodegón por grupo. No auto-agrupa. |
| Escala inicial | **Cientos–1000 por lote** → async (colas + workers), storage en nube, DB. |
| Quitar fondo | **Modelo local BiRefNet/rembg en worker GPU** (sin costo por imagen a escala). |
| Fondos de escena | **Librería de plantillas curadas por estilo** (no IA generativa al inicio). |

## Pipeline
```
1. INGESTA       lote (zip/multi-archivo) → archivo "SKU.png" → storage + índice SKU→imagen
2. NORMALIZA     worker: ¿alpha? sí → trim ; no → BiRefNet quita fondo → trim
                 + features (color dominante, orientación, ratio)
3. GRUPOS        CSV  grupo,sku  → resuelve SKU → imagen
4. GENERA        por grupo: estrategias (8) + IA (orden) + plantilla de fondo → N propuestas
5. REVISA/EXPORTA navegar por grupo → aprobar → descargar zip
```

## Stack por fase
| Capa | F1 (MVP batch) | F2 (plataforma) |
|---|---|---|
| Cola/async | tabla jobs SQLite + worker loop | Redis + RQ/Celery |
| Storage | disco local | S3 / Azure Blob |
| DB | SQLite | Postgres |
| Quitar fondo | rembg (CPU para probar) | BiRefNet en worker GPU |
| Fondos | carpeta de plantillas + JSON por estilo | + editor de estilos |
| UI | mínima (lote + CSV + resultados) | mockup completo |
| Auth/Pro | — | login + créditos + billing |

## Roadmap
- **F1 — Motor batch por SKU** (sobre el código actual): carga masiva, normaliza
  (rembg condicional + trim), grupos por CSV, genera por grupo con plantilla de
  fondo, export zip. Sin auth ni UI de plataforma. Valida el pipeline a escala.
- **F2 — Plataforma del mockup**: auth, proyectos, DB, storage en nube, workers a
  escala, UI pulida.
- **F3 — Monetización/integraciones**: créditos/Pro, fondos IA opcionales,
  conectores Shopify/PIM.

## Riesgo principal a validar primero
La **normalización a escala**: que rembg + trim deje packshots limpios y
consistentes desde imágenes mezcladas. Si eso falla, todo lo demás se ve mal.
