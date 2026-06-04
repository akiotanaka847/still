# StillAI
### *Still perfect. Every time.*

Herramienta web para composición inteligente de imágenes de producto.

## Módulos

**Bodegón Generator** *(principal)* — Genera composiciones de productos automáticamente con 10 estrategias de layout + propuesta IA usando Claude Vision. Recorta cada producto a su contenido real (al ras), añade sombras de contacto y composición con profundidad (incl. variantes Héroe XL y Escalonada). UI de plataforma en dos columnas.

**Smart Objects** *(legado, oculto)* — Detecta y reemplaza SmartObjectLayers en archivos `.psd` / `.psb` sin abrir Photoshop. El backend sigue activo; la pestaña está oculta desde el evolutivo 3.

## Stack

- **Backend:** FastAPI + PhotoshopAPI + Pillow
- **IA:** Claude Sonnet (Anthropic) — análisis visual de productos
- **Frontend:** Vanilla JS + Tailwind CSS

## Inicio rápido

```bash
pip install -r requirements.txt
# Crear .env con ANTHROPIC_API_KEY=sk-...
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
| **✦ IA** | Claude Vision analiza y sugiere el orden óptimo |
