# Ramas: qué es cada una y por qué

Este repo es **un producto**: **Bodegón** — compone fotos de producto en
bodegones. `layout.py`, `ai_proposal.py`, `renderer.py`, `image_gen.py`.

> **Promos se retiró del código activo el 2026-09-03.** Vivió acá del 1 al 3 de
> septiembre. Queda archivado en el tag `promos-archivo-2026-09-03`, que
> conserva el módulo completo, el motor declarativo de mecánicas, la guía de
> OXXO y las pruebas de navegador. Para recuperarlo:
> `git checkout -b promos promos-archivo-2026-09-03`.
> El origen del código es `github.com/lominet2020/Generador-de-Promo`.

## Las ramas

| rama | para qué | quién la mueve |
|---|---|---|
| `bodegon` | trabajo en curso del módulo de Bodegón | commits directos |
| `master` | integración: lo terminado y probado | solo por merge |
| `production` | lo que corre para el cliente | solo fast-forward desde `master` |
| `gpt-image-2.5` | evaluar Sunburst y Flare como motor generativo | commits directos |

El flujo es siempre en un solo sentido:

```
gpt-image-2.5 ─┐
               ├─→ bodegon ─→ master ─→ production
   (otras)    ─┘
```

`gpt-image-2.5` es una **rama de evaluación**, no de entrega: existe para medir
si los modelos nuevos de imagen mejoran el motor generativo antes de tocar nada
que se use. Se fusiona a `bodegon` solo si las mediciones lo justifican; si no,
se archiva con un tag y se borra. Lo que se aprenda queda escrito en
`tests/bodegon/README.md` aunque el código no sobreviva.

**`production` no es una rama de desarrollo, es un puntero de despliegue.**
Nunca se commitea ahí ni se fusiona nada que no venga de `master`. Si alguna vez
`production` tiene algo que `master` no tenga, eso es un bug de proceso.

**Desplegar a `production` requiere confirmación explícita cada vez.** Que algo
esté en `master` significa "terminado y probado", no "publicado".

## Archivos con cuidados propios

| archivo | por qué |
|---|---|
| `renderer.py` | `compose_psd()` se extrajo de `render_psd` cuando existía Promos, para reusarlo. Se quedó porque `render_psd` ya lo usa: es la ruta de PSD de Bodegón. No tiene pruebas. |
| `static/css/tailwind.css` | salida compilada. Ver `.gitattributes` — se resuelve con el driver `keepours` y se recompila después de cada merge. |
| `requirements.txt` | `psd-tools` está en pin exacto porque `render_psd` usa atributos internos para esquivar un bug de la librería. No subir de versión sin volver a probar. |

## Rutina de merge a `master`

```bash
git checkout master
git merge bodegon
npm run build:css            # tailwind.css quedó del lado de master: recompilar
git add static/css/tailwind.css
.venv/bin/python -m pytest tests/ -q
git push origin master
```
