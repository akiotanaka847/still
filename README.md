# StillAI
### *Still perfect. Every time.*

Herramienta web para composición inteligente de imágenes de producto.

## Módulos

**Smart Objects** — Detecta y reemplaza SmartObjectLayers en archivos `.psd` / `.psb` sin abrir Photoshop.

**Bodegón Generator** — Genera composiciones de productos automáticamente con 6 estrategias de layout + propuesta IA usando Claude Vision.

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
| **✦ IA** | Claude Vision analiza y sugiere el orden óptimo |
