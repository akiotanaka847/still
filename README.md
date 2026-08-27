# StillAI
### *Still perfect. Every time.*

Herramienta web para composición inteligente de imágenes de producto.

## Módulos

**Bodegón Generator** *(principal)* — Genera composiciones de productos automáticamente con
**30 estrategias de layout** (11 con propuesta ✦ IA equivalente) + análisis de visión
multi-proveedor (Groq / Gemini / Claude / OpenAI) que decide orden, tamaño real, y —desde v2—
también posición/ángulo ("IA · Libre") y la zona a resaltar en el **zoom de detalle**. Recorta
cada producto a su contenido real (al ras), añade sombras de contacto, y opcionalmente compone
sobre una **galería de fondos de estudio** generados por IA (con reflejo + luz de borde). Detalle
completo de todo esto en [`docs/GENERADOR_BODEGON.md`](docs/GENERADOR_BODEGON.md). UI de
plataforma en dos columnas.

**Smart Objects** *(legado, oculto)* — Detecta y reemplaza SmartObjectLayers en archivos `.psd` / `.psb` sin abrir Photoshop. El backend sigue activo; la pestaña está oculta desde el evolutivo 3.

## Stack

- **Backend:** FastAPI + Pillow
- **IA de análisis:** Groq, Google Gemini, Anthropic Claude u OpenAI (configurable, `AI_PROVIDER` en `.env`)
- **Frontend:** Vanilla JS + Tailwind CSS (compilado localmente, sin CDN)

## Inicio rápido

```bash
pip install -r requirements.txt
# Crear .env con al menos una de: GROQ_API_KEY / GEMINI_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY
uvicorn main:app --port 8000
```

O doble clic en `start.bat` (Windows).

## Estrategias de layout

| Estrategia | Lógica |
|---|---|
| Auto | Escala perceptual, verticales con boost |
| Por área | Mayor superficie primero |
| Alternado | Ritmo visual alto/ancho |
| Centrado | El más alto al centro de cada fila |
| Uniforme | Todos la misma altura |
| Dinámico | Contraste acentuado |
| Sombra | Productos anclados con sombra de contacto |
| Profundidad | Héroe al centro más grande, con solapamiento y sombra |
| Héroe XL | Producto central dominante (escala 1.5) |
| Escalonada | Tamaño decrece del centro hacia los lados (efecto abanico) |
| Impacto | Abanico de inclinaciones hacia afuera del héroe (hasta 30°) |
| Fila recta | Derechos en línea, sin héroe centrado |
| Cruce | Inclinados uno hacia el otro, convergen al centro |
| Inclinado ★ | Todos al mismo ángulo fijo (30° o 45°), izquierda o derecha |
| Cascada | Misma inclinación para todos, crece en diagonal |
| Radial | Alturas/ángulos variados, irradia desde el centro |
| Flotante | Cada uno a su propia altura, sin línea de base compartida |
| **✦ IA** | Analiza los productos y sugiere orden + tamaño real para cualquiera de las anteriores |
| **✦ IA · Libre** | La IA propone la composición completa (posición + ángulo) directamente |

Detalle de cada una, más los fondos de estudio y el zoom de detalle: [`docs/GENERADOR_BODEGON.md`](docs/GENERADOR_BODEGON.md).
