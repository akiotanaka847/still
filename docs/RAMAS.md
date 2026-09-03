# Ramas: qué es cada una y por qué

Este repo tiene **dos productos**, no uno con dos pantallas:

- **Bodegón** — compone fotos de producto en bodegones. `layout.py`,
  `ai_proposal.py`, `image_gen.py`.
- **Promos** — convierte filas de Excel/JSON en piezas de promoción.
  `promo*.py`, `static/js/promo*.js`, `static/promo/`.

No comparten flujo, ni cliente, ni datos. Sí comparten proceso, sesión y
archivo de UI. Las ramas existen para que el trabajo de uno no arrastre al otro
hasta que esté listo.

## Las cuatro ramas

| rama | para qué | quién la mueve |
|---|---|---|
| `bodegon` | trabajo en curso del módulo de Bodegón | commits directos |
| `promos` | trabajo en curso del módulo de Promos | commits directos |
| `master` | integración: lo que está terminado y probado, de los dos | solo por merge |
| `production` | lo que corre para el cliente | solo fast-forward desde `master` |

El flujo es siempre el mismo y en un solo sentido:

```
bodegon ─┐
         ├─→ master ─→ production
promos  ─┘
```

**`production` no es una rama de desarrollo, es un puntero de despliegue.** Nunca
se commitea ahí ni se fusiona nada que no venga de `master`. Si alguna vez
`production` tiene algo que `master` no tenga, eso es un bug de proceso, no una
variante.

**Desplegar a `production` requiere confirmación explícita cada vez.** Que algo
esté en `master` significa "terminado y probado", no "publicado".

## Los cuatro archivos compartidos

Casi todo es exclusivo de un módulo, y por eso esta separación funciona. Las
excepciones importan:

| archivo | por qué lo comparten |
|---|---|
| `renderer.py` | **infraestructura de los dos.** Promos extrajo `compose_psd()` de `render_psd` para reusarlo. Un cambio acá afecta el PSD de Bodegón *y* de Promos, y el archivo no tiene pruebas: cambiarlo desde una rama de módulo pide verificar las dos salidas. |
| `main.py` | Promos solo aporta 3 puntos: `PROTECTED_PREFIXES`, el `font-src data:` del CSP y el montaje de sus routers al final del archivo. Todo lo demás es de Bodegón. En la práctica no colisionan. |
| `static/index.html` | tiene la UI de los dos, pero en **regiones separadas y contiguas**, no entremezcladas. Mantenerlas así es lo que evita el conflicto; interleavarlas lo garantiza. |
| `static/css/tailwind.css` | salida compilada. Ver `.gitattributes` — se resuelve con el driver `keepours` y se recompila después de cada merge. |

## Rutina de merge a `master`

```bash
git checkout master
git merge bodegon            # o promos
npm run build:css            # tailwind.css quedó del lado de master: recompilar
git add static/css/tailwind.css
git commit --amend --no-edit # si el merge no dejó conflicto que ya haya commiteado
.venv/bin/python -m pytest tests/ -q
git push origin master
```

Las pruebas de navegador (`tests/navegador/`) no corren solas: necesitan el
servidor arriba y el usuario `testgen_still_ai`. Para cambios en Promos que
toquen el dibujo, correr al menos `equivalencia_declarativa.py` y
`regresion_excel_128.py` antes de fusionar.

## Historia

`promos` se llamaba `nuevoenfoquepromos` y trae el motor declarativo de
mecánicas detrás del flag `PROMO_FLOW_DECLARADO`, apagado por defecto. Ver
`CONTEXTO_NUEVOENFOQUEPROMOS.md` para qué está probado y qué falta.
