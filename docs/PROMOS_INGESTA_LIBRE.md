# Promos · Ingesta libre (experimental)

Cómo alimentar el generador de promos desde **JSON** en vez de Excel.

> Estado: experimental. El módulo de Promos con Excel sigue funcionando igual
> que siempre y no se ve afectado por esto.

## Por qué

El generador de promos producía las piezas siempre a partir de un `.xlsx`.
Esto permite que los datos lleguen desde cualquier lado — un JSON exportado de
otro sistema, un catálogo, un script — sin depender de una planilla.

Las promociones que salen son **exactamente las mismas**: mismo motor de
dibujo, mismas plantillas, mismos 17 casos. Lo único que cambia es de dónde
vienen los datos.

## Formato

Un objeto con `rows`, donde cada fila es una promo:

```json
{
  "name": "Promos septiembre",
  "rows": [
    {
      "ORIENTACION": "V",
      "Nombre corto": "coca-2l",
      "CALLOUT": "CA_NUEVO.svg",
      "FRASE APOYO": "ÚLTIMOS DÍAS POR SOLO",
      "PESOS O PRECIO": "45",
      "DESCRIPCION": "Coca-Cola 2L",
      "BRAND": "CocaCola.svg"
    }
  ]
}
```

También se acepta una lista suelta de filas (`[ {...}, {...} ]`), que es lo que
suele mandar un sistema externo.

Los nombres de campo son **los mismos que las columnas del Excel**, y se
emparejan igual: sin distinguir mayúsculas, tildes ni espacios (`FRASE APOYO`,
`frase apoyo` y `FRASEAPOYO` son lo mismo).

### Campos disponibles

| Campo | Para qué sirve |
|---|---|
| `ORIENTACION` | `V` u `H` — decide la plantilla de esa fila |
| `Nombre corto` | Nombre del archivo generado |
| `CALLOUT`, `BRAND` | Nombre del archivo de imagen (debe existir en la biblioteca) |
| `FRASE APOYO`, `DESCRIPCION`, `DESCRIPCION 2` | Texto |
| `MULTIPLICADOR`, `PESOS O PRECIO`, `DESCUENTO`, `MULTIPLICADOR 2`, `PESOS O PRECIO 2` | Se combinan en el precio |

Qué promoción sale se decide **según qué campos vengan con datos** — igual que
con el Excel. No hay que declarar el "tipo" de promo.

## Texto con formato

Hay dos formas, y se pueden mezclar en el mismo archivo.

### 1. Marcas dentro del texto (lo más simple)

```json
"FRASE APOYO": "*LLEVA* 3 por ~$50~ ^c/u^"
```

| Marca | Resultado |
|---|---|
| `*texto*` | **negrilla** |
| `~texto~` | ~~tachado~~ |
| `^texto^` | superíndice |

### 2. Tramos explícitos (cuando hace falta color por palabra)

```json
"DESCRIPCION": {
  "runs": [
    { "text": "Coca-Cola " },
    { "text": "2L", "bold": true, "color": "rgb(220,39,38)" }
  ]
}
```

Claves de cada tramo: `text`, `bold`, `italic`, `strike`, `superscript`,
`subscript`, `color`. Solo `text` es obligatorio.

## Porcentajes: la única diferencia real con Excel

En Excel, un `25%` se guarda internamente como `0.25` con formato de
porcentaje, y el sistema lo detecta solo. En JSON no existe ese formato, así
que hay dos maneras:

```json
"DESCUENTO": "25%"                              // texto ya formateado
"DESCUENTO": { "value": 0.25, "percent": true } // valor + declaración
```

**Si mandás `0.25` sin `"percent": true`, no se interpreta como porcentaje.**
Es el error más fácil de cometer.

## Imágenes

`CALLOUT` y `BRAND` se escriben con el **nombre del archivo**, que tiene que
existir en la biblioteca de imágenes de marca (se sube desde la pantalla de
Promos). Si el nombre no existe, la promo se genera igual pero con la imagen
de error.

## Uso

**Desde la pantalla:** Promos → sección "Ingesta libre" → subir el `.json`.

**Desde otro sistema:** hay dos endpoints, ambos detrás de la sesión de Still.

```
POST /api/promo-lab/read          una sola promo (la primera fila)
POST /api/promo-lab/read-batch    todas las filas
```

Aceptan el JSON como archivo (campo `file`) o en línea (campo `data`), lo que
permite integrarlos sin construir un archivo:

```bash
curl -X POST http://localhost:8000/api/promo-lab/read-batch \
     -b cookies.txt \
     --data-urlencode 'data={"rows":[{"PESOS O PRECIO":"45","DESCRIPCION":"Ruffles"}]}'
```

Devuelven los campos ya resueltos (incluido el precio calculado), listos para
que el navegador los dibuje.

`GET /api/promo-lab/sources` lista los formatos soportados hoy.

## Limitación conocida

Estos endpoints devuelven los **datos resueltos**, no la imagen final: el
dibujo ocurre en el navegador, porque es lo que permite que la negrilla, el
tachado y el superíndice por palabra salgan exactos. Un sistema externo que
quiera recibir el PNG directamente necesita que Still renderice del lado del
servidor — está diseñado (Chromium headless reusando el mismo motor) pero
todavía no implementado, porque obliga a meter un navegador en el contenedor
de despliegue.
