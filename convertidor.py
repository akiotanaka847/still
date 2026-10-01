"""Convertidor: una plantilla de Photoshop entra, una plantilla HTML sale.

El problema que resuelve: las plantillas llegan como las nombra un disenador
—"49", "2x", "Vigencia del 27 de Agosto...", "SUAJE"— y no con una convencion
que una expresion regular pueda leer. El extractor que vive en
Automatizador-Promos exige nombres tipo TEXT_PRECIO_ y por eso saca 0 campos de
un PSD real de produccion (medido: 0 de 42 capas en "Cabecera 5 Central B").

La salida es un HTML autocontenido donde cada capa del PSD es un elemento
posicionado, apilado en el mismo orden que en Photoshop. La pieza se ve igual
que el original —eso es lo que la vuelve reconocible— y ademas cada elemento
variable queda marcado y es editable. Desde ahi se convierte a lo que haga
falta, porque un navegador ya sabe llevarlo a PNG o a PDF.

Un fondo unico aplanado NO sirve, y no por teoria: en "Cabecera 5 Central B" la
capa FileName vive debajo de ESTRUCTURA, que la tapa. Aplanando lo fijo en una
imagen, FileName acababa pintada encima del precio.

TRES PASOS, SEPARADOS A PROPOSITO
---------------------------------
1. analizar()  Determinista. Lee lo que el PSD declara: geometria, tipo de
               capa, tipografia, enlaces de objetos inteligentes, que grupos
               estan ocultos. Nada de esto se adivina, asi que no puede estar
               mal: o el dato esta en el archivo o no esta.

2. proponer_roles()  Asistido por IA. Decide que ES cada capa (producto,
               precio, vigencia, legal...) y le pone un nombre canonico. Esto
               SI es interpretacion, y por eso vive aparte: se puede revisar,
               corregir y volver a correr sin tocar el paso 1. Si no hay clave
               de IA, cae a heuristicas y marca el resultado como inseguro.

3. a_html()    Compone. Sin decisiones propias: pinta lo que los dos pasos
               anteriores dejaron dicho.

La separacion importa porque el paso 2 es el unico que puede equivocarse. Si
manana cambia el modelo o se afina el prompt, los pasos 1 y 3 no se tocan.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Optional

from PIL import Image

# Las plantillas de imprenta son enormes de verdad (hay una de 29500x6466, unos
# 190 millones de pixeles). Pillow las rechaza por defecto como sospechosas de
# "bomba de descompresion". Son archivos legitimos del cliente, asi que se
# levanta el limite a proposito y no por descuido.
Image.MAX_IMAGE_PIXELS = None


# ─── Vocabulario de roles ─────────────────────────────────────────────────────
# Cerrado a proposito. Un vocabulario abierto deja que el modelo invente
# sinonimos ("precio", "price", "importe") y entonces nada de lo que viene
# despues puede confiar en el valor. Si falta un rol, se agrega aqui.
ROLES = {
    "producto":      "La foto del producto que se vende",
    "precio":        "La cifra del precio",
    "cantidad":      "Multiplicador tipo 2x, 3x",
    "moneda":        "El simbolo $ o similar",
    "descripcion":   "Nombre y presentacion del producto",
    "promo":         "Grafico o texto del mecanismo de la promocion",
    "callout":       "Globo o sello que destaca algo",
    "frase_apoyo":   "Frase corta de refuerzo",
    "vigencia":      "Fechas de validez de la promocion",
    "legal":         "Texto legal, advertencias, leyendas obligatorias",
    "marca":         "Logo de la marca del producto",
    "logo_tienda":   "Logo de la cadena o punto de venta",
    "qr":            "Codigo QR",
    "codigo_barras": "Codigo de barras o folio",
    "senalizacion":  "Identificador interno de la pieza y su ubicacion",
    "fondo":         "Imagen o color de fondo",
    "estructura":    "Marco, recuadro o elemento grafico fijo",
    "imprenta":      "Guia de produccion: suaje, corte, sangrado, registro",
    "decorativo":    "Adorno sin contenido variable",
}

# Lo unico que se clasifica sin preguntarle a nadie. La lista es corta a
# proposito: solo terminos de produccion grafica que no significan otra cosa.
# "PLECA" o "CENEFA", por ejemplo, NO estan aqui — son elementos de marca y
# confundirlos con guias de imprenta borraria contenido real.
_IMPRENTA = re.compile(
    r"\b(suaje|troquel|corte|sangrado|sangrad|registro|"
    r"marcas?\s+de\s+corte|area\s+de\s+protecc|proteccion\s+grapa)\b",
    re.IGNORECASE,
)

# Un objeto inteligente que se llama "7501011123588_Doritos Nacho 58 gr" no
# necesita que nadie adivine: ese numero es el codigo de barras del producto.
_SKU = re.compile(r"\b(\d{8,14})[_\s-]")

_EDITABLES = {
    "producto", "precio", "cantidad", "descripcion", "promo", "callout",
    "frase_apoyo", "vigencia", "legal", "marca", "qr", "codigo_barras",
    "senalizacion",
}


def _sin_tildes(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def _limpiar_nombre(s: str) -> str:
    """Un nombre de capa cualquiera -> identificador usable en codigo."""
    s = _sin_tildes(s or "").lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return re.sub(r"_+", "_", s)[:40] or "capa"


# ─── Paso 1: analizar (determinista) ─────────────────────────────────────────
def _caja_y_estilo_texto(capa) -> tuple[dict, Optional[str], Optional[float], Optional[str]]:
    """Caja real y tipografia de una capa de texto.

    Usa boundingBox del EngineData y no el bbox de pixeles. El motivo es real:
    si el texto de ejemplo viene vacio —pasa con los campos que el disenador
    deja en blanco— el bbox de pixeles es (0,0,0,0) y el hueco desapareceria
    de la plantilla justo por estar vacio, que es cuando mas falta hace.

    Metodo tomado de Automatizador-Promos/tools/extract_template.py, ya probado
    contra plantillas reales.
    """
    try:
        a, b, c, d, tx, ty = capa.transform
        raw = capa._data.text_data
        bb = raw.get(b"boundingBox") or raw.get(b"bounds")
        izq, arr, der, aba = bb[b"Left"], bb[b"Top "], bb[b"Rght"], bb[b"Btom"]
        x0, y0 = a * izq + c * arr + tx, b * izq + d * arr + ty
        x1, y1 = a * der + c * aba + tx, b * der + d * aba + ty
        caja = {
            "x": round(min(x0, x1), 1), "y": round(min(y0, y1), 1),
            "w": round(abs(x1 - x0), 1), "h": round(abs(y1 - y0), 1),
        }
    except Exception:
        caja = {"x": float(capa.left), "y": float(capa.top),
                "w": float(capa.width), "h": float(capa.height)}

    familia = tamano = color = None
    try:
        est = capa.engine_dict["StyleRun"]["RunArray"][0]["StyleSheet"]["StyleSheetData"]
        fuentes = capa.resource_dict.get("FontSet")
        if fuentes:
            familia = fuentes[est.get("Font", 0)].get("Name").value
        a, _b, _c, d, _tx, _ty = capa.transform
        escala = (abs(a) + abs(d)) / 2.0
        tamano = round(est.get("FontSize", 0) * escala, 1)
        relleno = est.get("FillColor")
        if relleno and relleno.get("Values") and len(relleno["Values"]) >= 4:
            _alfa, r, g, bl = relleno["Values"][:4]
            color = f"rgb({round(r*255)},{round(g*255)},{round(bl*255)})"
    except Exception:
        pass
    return caja, familia, tamano, color


def analizar(ruta: str | Path) -> dict[str, Any]:
    """Lee un PSD y describe su estructura. No interpreta nada."""
    from psd_tools import PSDImage

    ruta = Path(ruta)
    psd = PSDImage.open(ruta)
    capas: list[dict] = []
    contador = [0]

    def recorrer(grupo, camino: list[str], oculto_arriba: bool) -> None:
        for capa in grupo:
            contador[0] += 1
            ident = f"c{contador[0]}"
            nombre = capa.name or ""
            oculto = oculto_arriba or not capa.visible
            clase = capa.kind                       # type / smartobject / pixel / shape / group
            d: dict[str, Any] = {
                "id": ident,
                "nombre": nombre,
                "ruta": " / ".join(camino + [nombre]),
                "clase": clase,
                "visible": bool(capa.visible),
                "oculto_por_grupo": oculto_arriba,
                "caja": {"x": float(capa.left), "y": float(capa.top),
                         "w": float(capa.width), "h": float(capa.height)},
            }
            if capa.opacity != 255:
                d["opacidad"] = round(capa.opacity / 255, 3)

            if clase == "type":
                caja, familia, tamano, color = _caja_y_estilo_texto(capa)
                d["caja"] = caja
                try:
                    d["texto"] = (capa.text or "").replace("\r", "\n")
                except Exception:
                    d["texto"] = ""
                d["fuente"] = familia
                d["tamano"] = tamano
                d["color"] = color
            elif clase == "smartobject":
                try:
                    d["enlace"] = capa.smart_object.filename
                except Exception:
                    d["enlace"] = None
                sku = _SKU.search(nombre) or _SKU.search(d.get("enlace") or "")
                if sku:
                    d["sku"] = sku.group(1)

            # Lo unico que se decide sin IA.
            if _IMPRENTA.search(nombre):
                d["rol_fijo"] = "imprenta"

            capas.append(d)
            if capa.is_group():
                recorrer(capa, camino + [nombre], oculto)

    recorrer(psd, [], False)

    # Grupos ocultos = variantes de la misma plantilla. En el PSD de ejemplo,
    # "Vigencias" tiene dentro "Multiple" (visible) y "Simple" (oculta): no es
    # basura, es el mismo documento con dos modos. Tratarlos como variantes en
    # lugar de borrarlos es lo que permite que una plantilla sirva para varias
    # promos.
    variantes = [c for c in capas if c["clase"] == "group" and not c["visible"]]

    return {
        "archivo": ruta.name,
        "lienzo": {"ancho": psd.width, "alto": psd.height,
                   "modo": psd.color_mode.name},
        "capas": capas,
        "variantes": [v["ruta"] for v in variantes],
        "resumen": {k: sum(1 for c in capas if c["clase"] == k)
                    for k in sorted({c["clase"] for c in capas})},
    }


# ─── Paso 2: proponer roles (IA) ─────────────────────────────────────────────
_PROMPT = """Eres un director de arte revisando una plantilla de Photoshop para punto de venta.

Te doy las capas de un PSD real. Los nombres los puso un disenador, no siguen
ninguna convencion, y estan en espanol. Tu trabajo es decir QUE ES cada capa.

Pistas que valen mas que el nombre:
- El texto de ejemplo delata el rol: "38" o "49" sueltos junto a un "$" son el
  precio; "2x" es la cantidad; "Vigencia del 4 de..." es la vigencia; un parrafo
  largo en mayusculas con advertencias es el legal.
- Un objeto inteligente cuyo nombre empieza por 8-14 digitos es un PRODUCTO:
  ese numero es su codigo de barras.
- Capas llamadas SUAJE, CORTE, TROQUEL, SANGRADO, REGISTRO o PROTECCION son
  guias de imprenta, nunca contenido.
- Un texto pequeno con el nombre de la pieza y su ubicacion en tienda es
  senalizacion interna.
- Las formas y rectangulos grandes suelen ser estructura o fondo.

Devuelve SOLO un objeto JSON, sin texto alrededor, con esta forma:
{"capas": [{"id": "c1", "rol": "<uno de la lista>", "nombre": "<snake_case>", "confianza": 0.0-1.0, "porque": "<10 palabras max>"}]}

Roles permitidos (usa EXACTAMENTE estas cadenas):
%(roles)s

Reglas del nombre: snake_case, sin tildes, descriptivo y corto
(precio, producto_1, vigencia_multiple, legal_sabritas). Unico dentro de la plantilla.
Si no estas seguro, pon confianza baja; no inventes un rol que no esta en la lista.

CAPAS:
%(capas)s"""


def _resumen_para_ia(estructura: dict) -> str:
    filas = []
    for c in estructura["capas"]:
        p = [f'id={c["id"]}', f'clase={c["clase"]}', f'ruta="{c["ruta"]}"']
        caja = c["caja"]
        p.append(f'caja={caja["x"]:.0f},{caja["y"]:.0f} {caja["w"]:.0f}x{caja["h"]:.0f}')
        if not c["visible"]:
            p.append("OCULTA")
        if c.get("texto"):
            t = c["texto"].replace("\n", " ")[:70]
            p.append(f'texto="{t}"')
        if c.get("enlace"):
            p.append(f'enlace="{c["enlace"]}"')
        if c.get("sku"):
            p.append(f'sku={c["sku"]}')
        filas.append("  " + "  ".join(p))
    return "\n".join(filas)


def _heuristica(estructura: dict) -> dict[str, dict]:
    """Respaldo sin IA. Acierta poco a proposito: marca confianza baja para que
    quede claro que esto necesita una pasada humana, en vez de aparentar que
    la plantilla ya esta lista."""
    out = {}
    for c in estructura["capas"]:
        rol = c.get("rol_fijo")
        if not rol:
            if c.get("sku"):
                rol = "producto"
            elif c["clase"] == "type":
                t = (c.get("texto") or "").strip()
                bajo = _sin_tildes(t.lower())
                if bajo.startswith("vigencia"):
                    rol = "vigencia"
                elif re.fullmatch(r"\$|\d{1,3}|\d+x", t):
                    rol = "cantidad" if t.endswith("x") else ("moneda" if t == "$" else "precio")
                elif len(t) > 120:
                    rol = "legal"
                else:
                    rol = "descripcion"
            elif c["clase"] == "group":
                rol = "estructura"
            else:
                rol = "decorativo"
        out[c["id"]] = {
            "rol": rol,
            "nombre": _limpiar_nombre(c["nombre"]) or rol,
            "confianza": 0.3,
            "porque": "sin IA: heuristica",
        }
    return out


def proponer_roles(estructura: dict, api_key: Optional[str] = None,
                   modelo: str = "gpt-5.1") -> dict:
    """Anade rol, nombre limpio y confianza a cada capa.

    Devuelve la estructura con las capas enriquecidas. Marca `asistido` para
    que quien lea el resultado sepa si lo decidio un modelo o una heuristica.
    """
    api_key = api_key or os.environ.get("OPENAI_API_KEY")
    propuesta, asistido = None, False

    if api_key:
        try:
            from openai import OpenAI
            cliente = OpenAI(api_key=api_key)
            prompt = _PROMPT % {
                "roles": "\n".join(f"  {k}: {v}" for k, v in ROLES.items()),
                "capas": _resumen_para_ia(estructura),
            }
            r = cliente.chat.completions.create(
                model=modelo,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
            datos = json.loads(r.choices[0].message.content)
            propuesta = {c["id"]: c for c in datos.get("capas", [])}
            asistido = True
        except Exception as e:                      # la plantilla vale mas que el modelo
            estructura["aviso_ia"] = f"{type(e).__name__}: {e}"

    if not propuesta:
        propuesta = _heuristica(estructura)

    usados: set[str] = set()
    for c in estructura["capas"]:
        p = propuesta.get(c["id"], {})
        rol = p.get("rol")
        if c.get("rol_fijo"):                       # el nombre manda sobre la IA
            rol = c["rol_fijo"]
        if rol not in ROLES:
            rol = "decorativo"
        nombre = _limpiar_nombre(p.get("nombre") or c["nombre"]) or rol
        base, n = nombre, 2
        while nombre in usados:                     # nombres unicos, siempre
            nombre, n = f"{base}_{n}", n + 1
        usados.add(nombre)

        c["rol"] = rol
        c["nombre_limpio"] = nombre
        c["confianza"] = float(p.get("confianza", 0.3))
        c["porque"] = p.get("porque", "")
        c["editable"] = rol in _EDITABLES and c["clase"] in ("type", "smartobject", "pixel")

    estructura["asistido_por_ia"] = asistido
    return estructura


# ─── Paso 3: componer la plantilla HTML ──────────────────────────────────────
def rasterizar(ruta_psd: str | Path, estructura: dict,
               ancho_max: int = 2000) -> dict[str, str]:
    """Cada capa por separado, en PNG con transparencia, lista para el HTML.

    La primera version aplanaba todo lo no editable en UNA imagen de fondo y
    pintaba los huecos encima. Eso rompe el orden de pintado, y no de forma
    teorica: en "Cabecera 5 Central B" la capa FileName vive DEBAJO de
    ESTRUCTURA, que la tapa por completo. Con un fondo plano, ESTRUCTURA
    quedaba en el fondo y FileName se colaba encima del precio.

    Aqui cada capa es su propio elemento y el HTML las apila en el mismo orden
    que Photoshop, asi que lo que tapa sigue tapando. `estructura["capas"]` ya
    viene en orden de pintado —de abajo hacia arriba— porque psd-tools recorre
    los hijos en ese orden y analizar() respeta el recorrido.
    """
    from psd_tools import PSDImage

    psd = PSDImage.open(ruta_psd)
    escala = min(1.0, ancho_max / psd.width)
    por_ruta: dict[str, Any] = {}

    def recoger(grupo, camino):
        for capa in grupo:
            ruta = " / ".join(camino + [capa.name or ""])
            por_ruta[ruta] = capa
            if capa.is_group():
                recoger(capa, camino + [capa.name or ""])

    recoger(psd, [])

    salida: dict[str, str] = {}
    for c in estructura["capas"]:
        if c["clase"] == "group":
            continue
        capa = por_ruta.get(c["ruta"])
        if capa is None:
            continue
        try:
            img = capa.composite()                  # con sus efectos aplicados
        except Exception:
            img = None
        if img is None or img.width < 1 or img.height < 1:
            continue
        img = img.convert("RGBA")
        if escala < 1:
            img = img.resize((max(1, round(img.width * escala)),
                              max(1, round(img.height * escala))), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        salida[c["id"]] = base64.b64encode(buf.getvalue()).decode()
        # La caja que vale es la del render, no la declarada: una capa con
        # sombra o resplandor ocupa mas de lo que dice su bbox.
        c["caja_render"] = {"x": float(capa.left), "y": float(capa.top),
                            "w": float(capa.width), "h": float(capa.height)}
    estructura["escala_render"] = escala
    return salida


def a_html(estructura: dict, rasters: dict[str, str],
           titulo: Optional[str] = None) -> str:
    """Compone la plantilla: una capa por elemento, en orden de pintado.

    La pieza se ve igual que el PSD porque cada capa va con sus propios pixeles
    y en su misma posicion de la pila. Los huecos editables no se vacian —se
    marcan—, que es lo que permite reconocer la pieza y cambiarla a la vez.

    El lienzo conserva las coordenadas del PSD y solo se escala para verse, asi
    que un hueco sigue valiendo lo mismo al volver al PSD o al salir a PDF.
    """
    L = estructura["lienzo"]
    titulo = titulo or Path(estructura["archivo"]).stem

    capas, huecos = [], []
    for c in estructura["capas"]:
        if c["clase"] == "group":
            continue
        # Una capa puede estar apagada por si misma o por el grupo que la
        # contiene. Mirar solo lo primero dejaba las dos vigencias del ejemplo
        # —Multiple y Simple— pintadas una encima de otra.
        c["_variante"] = (not c["visible"]) or c.get("oculto_por_grupo", False)
        capas.append(c)
        if c.get("editable"):
            huecos.append(c)
    variantes = [c for c in capas if c["_variante"]]

    def bloque(c: dict) -> str:
        png = rasters.get(c["id"])
        caja = c.get("caja_render") or c["caja"]
        est = (f'left:{caja["x"]:.1f}px; top:{caja["y"]:.1f}px; '
               f'width:{caja["w"]:.1f}px; height:{caja["h"]:.1f}px')
        editable = c.get("editable")
        clases = "capa" + (" hueco" if editable else "")
        attrs = (f'data-rol="{c["rol"]}" data-nombre="{c["nombre_limpio"]}" '
                 f'data-capa="{_esc(c["nombre"])}" data-confianza="{c["confianza"]:.2f}"')
        if c.get("_variante"):
            attrs += ' data-variante="oculta"'
        # El suaje define el contorno real de la pieza: quitarlo deja un
        # rectangulo donde el original tiene una silueta troquelada. Se pinta
        # por defecto y se puede apagar, en vez de decidir por quien mira.
        if c.get("rol") == "imprenta":
            attrs += ' data-imprenta="1"'
        if c.get("sku"):
            attrs += f' data-sku="{c["sku"]}"'
        if c.get("opacidad"):
            est += f'; opacity:{c["opacidad"]}'

        # El texto viaja dos veces: como pixeles, que es lo fiel, y como texto
        # vivo con su tipografia y color, que es lo editable. La tipografia del
        # PSD casi nunca esta instalada en quien mira, asi que mostrar solo lo
        # segundo deformaria la pieza. El interruptor decide cual se ve.
        if c["clase"] == "type":
            tam = c.get("tamano") or 16
            fam = c.get("fuente") or "sans-serif"
            col = c.get("color") or "#000"
            b = c["caja"]
            vivo = (f'<div class="texto-vivo" contenteditable="true" '
                    f'style="left:{b["x"]:.1f}px; top:{b["y"]:.1f}px; '
                    f'width:{b["w"]:.1f}px; font-size:{tam:.1f}px; '
                    f'font-family:\'{fam}\',sans-serif; color:{col}; '
                    f'line-height:1.08" {attrs}>{_esc(c.get("texto") or "")}</div>')
            img = (f'<img class="{clases} pixeles" style="{est}" {attrs} alt="" '
                   f'src="data:image/png;base64,{png}">') if png else ""
            return "    " + img + "\n    " + vivo

        if not png:
            return (f'    <div class="{clases} sin-pixeles" style="{est}" {attrs}>'
                    f'<span>{c["nombre_limpio"]}</span></div>')
        return (f'    <img class="{clases}" style="{est}" {attrs} alt="" '
                f'src="data:image/png;base64,{png}">')

    filas = "\n".join(bloque(c) for c in capas)
    inventario = "\n".join(
        f'      <tr><td>{c["nombre_limpio"]}</td><td><code>{c["rol"]}</code></td>'
        f'<td>{c["clase"]}</td><td class="n">{c["confianza"]:.2f}</td>'
        f'<td class="o">{_esc(c["nombre"])}</td></tr>'
        for c in sorted(huecos, key=lambda x: (x["caja"]["y"], x["caja"]["x"]))
    )
    aviso = ("" if estructura.get("asistido_por_ia") else
             '<p class="aviso">Roles asignados por heuristica, sin IA. '
             'Revisa antes de usar.</p>')

    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(titulo)}</title>
<style>
  :root {{ --ancho:{L["ancho"]}px; --alto:{L["alto"]}px; --escala:1; }}
  * {{ box-sizing:border-box }}
  body {{ margin:0; background:#eceef1; color:#17191d;
         font:14px/1.5 ui-sans-serif,system-ui,sans-serif }}
  header {{ padding:20px 24px; background:#fff; border-bottom:1px solid #dcdfe4;
            display:flex; gap:20px; align-items:baseline; flex-wrap:wrap }}
  h1 {{ margin:0; font-size:17px; letter-spacing:-.01em }}
  .dato {{ color:#6b7280; font-size:12.5px }}
  .dato b {{ color:#17191d; font-variant-numeric:tabular-nums }}
  .aviso {{ margin:0; padding:8px 14px; background:#fdf3d6; color:#8a6200;
            border-radius:6px; font-size:13px }}
  main {{ padding:24px; overflow-x:auto }}
  .lienzo {{ position:relative; width:var(--ancho); height:var(--alto);
             transform:scale(var(--escala)); transform-origin:0 0; background:#fff;
             box-shadow:0 2px 24px -8px rgba(0,0,0,.35); outline:1px solid #cfd3da }}
  .marco {{ overflow:hidden; margin:0 auto }}
  /* Cada capa en su sitio y en su orden. El apilado lo da el orden del
     documento, que es el mismo de Photoshop: lo que tapa, sigue tapando. */
  .capa {{ position:absolute; display:block }}
  img.capa {{ image-rendering:auto }}
  .sin-pixeles {{ display:flex; align-items:center; justify-content:center;
                  background:rgba(74,91,255,.07) }}
  .sin-pixeles span {{ font:600 12px/1.2 ui-monospace,monospace; color:#3b49c9;
                       background:#fff; padding:4px 8px; border-radius:4px }}
  /* El texto vivo se superpone al rasterizado. Oculto por defecto: la
     tipografia del PSD casi nunca esta instalada y la pieza se deformaria. */
  .texto-vivo {{ position:absolute; white-space:pre-wrap; display:none;
                 overflow-wrap:break-word }}
  body.texto-editable .texto-vivo {{ display:block }}
  body.texto-editable img.pixeles {{ visibility:hidden }}
  .texto-vivo:focus {{ outline:2px solid #4a5bff; background:rgba(74,91,255,.07) }}
  /* Las variantes son modos alternos de la MISMA plantilla —en este PSD,
     "Vigencias" trae una version Multiple y otra Simple—. Pintarlas a la vez
     deja la pieza ilegible, asi que por defecto solo se ve la activa. */
  [data-variante] {{ display:none !important }}
  body.con-variantes [data-variante] {{ display:block !important }}
  body.con-variantes.texto-editable img.pixeles[data-variante] {{ visibility:hidden }}
  /* El contorno es andamio, no plantilla: apagado, esto es la pieza limpia. */
  body.con-huecos .hueco {{ outline:1.5px dashed rgba(70,90,255,.6);
                            outline-offset:1px }}
  body.con-huecos .hueco[data-variante] {{ outline-color:rgba(190,120,0,.8) }}
  body.sin-imprenta [data-imprenta] {{ display:none !important }}
  .mandos {{ display:flex; gap:16px; margin-left:auto; align-items:center }}
  body.solo-lienzo > section, body.solo-lienzo > header {{ display:none }}
  body.solo-lienzo main {{ padding:12px }}
  .mandos label {{ display:flex; gap:6px; align-items:center; font-size:12.5px;
                   color:#4b5563; cursor:pointer; user-select:none }}
  table {{ border-collapse:collapse; width:100%; background:#fff; font-size:13px }}
  th,td {{ text-align:left; padding:7px 12px; border-bottom:1px solid #e6e9ee }}
  th {{ font-size:11px; text-transform:uppercase; letter-spacing:.07em; color:#6b7280 }}
  td.n {{ font-variant-numeric:tabular-nums; color:#6b7280 }}
  td.o {{ color:#8b919c; font-size:12px }}
  code {{ font:12px ui-monospace,monospace; background:#eef0f4; padding:1px 5px;
          border-radius:3px }}
  section {{ margin:24px; background:#fff; border:1px solid #dcdfe4; border-radius:10px;
             overflow:hidden }}
  section h2 {{ margin:0; padding:14px 16px; font-size:13px; text-transform:uppercase;
                letter-spacing:.07em; color:#6b7280; border-bottom:1px solid #e6e9ee }}
</style></head><body>
<header>
  <h1>{_esc(titulo)}</h1>
  <span class="dato">Lienzo <b>{L["ancho"]} x {L["alto"]}</b> px · {L["modo"]}</span>
  <span class="dato">Capas <b>{len(capas)}</b> · editables <b>{len(huecos)}</b></span>
  {aviso}
  <div class="mandos">
    <label><input type="checkbox" id="vh" checked> Ver huecos</label>
    <label><input type="checkbox" id="vv"> Ver variantes ({len(variantes)})</label>
    <label><input type="checkbox" id="te"> Texto editable</label>
    <label><input type="checkbox" id="vi" checked> Ver suaje</label>
  </div>
</header>
<main><div class="marco"><div class="lienzo" id="lienzo">
{filas}
  </div></div></main>
<section>
  <h2>Huecos de la plantilla</h2>
  <table><thead><tr><th>Nombre</th><th>Rol</th><th>Tipo</th><th>Confianza</th>
  <th>Capa original en el PSD</th></tr></thead><tbody>
{inventario}
  </tbody></table>
</section>
<script>
  // El lienzo conserva las coordenadas del PSD y solo se escala para verlo.
  // Asi un hueco sigue valiendo lo mismo al volver al PSD o al salir a PDF.
  const lienzo = document.getElementById('lienzo');
  const marco  = lienzo.parentElement;
  function ajustar() {{
    // Ajusta a lo ancho Y a lo alto. Con solo el ancho, una pieza vertical
    // —la DM_ELECTROVERTICAL mide 1181x4016— cabia de sobra a lo ancho, se
    // quedaba a escala 1 y solo se veia su primer cuarto.
    const ancho = marco.clientWidth || document.body.clientWidth - 48;
    const alto  = Math.max(320, innerHeight - 180);
    const e = Math.min(1, ancho / {L["ancho"]}, alto / {L["alto"]});
    lienzo.style.setProperty('--escala', e);
    marco.style.width  = ({L["ancho"]} * e) + 'px';
    marco.style.height = ({L["alto"]}  * e) + 'px';
  }}
  addEventListener('resize', ajustar); ajustar();

  // Incrustada en Still, la cabecera y el inventario estarian dos veces en
  // pantalla. Con #solo-lienzo la plantilla se queda con lo suyo: la pieza.
  if (location.hash === '#solo-lienzo') document.body.classList.add('solo-lienzo');

  // Dos interruptores, dos preguntas distintas: "muestrame donde van las cosas"
  // y "muestrame tambien los modos que este PSD trae apagados".
  const pintar = () => {{
    document.body.classList.toggle('con-huecos',     vh.checked);
    document.body.classList.toggle('con-variantes',  vv.checked);
    document.body.classList.toggle('texto-editable', te.checked);
    document.body.classList.toggle('sin-imprenta',   !vi.checked);
  }};
  [vh, vv, te, vi].forEach(x => x.addEventListener('change', pintar));
  pintar();
</script>
</body></html>"""


# ─── Salidas: el paquete portable y los tres formatos ────────────────────────
FORMATO = "still.plantilla"
FORMATO_VERSION = 1


def paquete(estructura: dict, rasters: dict[str, str]) -> dict:
    """La plantilla en un solo JSON autocontenido.

    Es el formato de intercambio: lleva TODO lo necesario para reconstruir la
    pieza —geometria, orden de pintado, tipografia y los pixeles de cada capa—
    sin depender de archivos sueltos ni de rutas. Un unico archivo que se sube
    a donde haga falta.

    Va versionado porque es un contrato con quien lo consuma: cuando cambie la
    forma, `version` sube y el otro lado puede decidir si entiende o no.

    `capas` viene en orden de pintado, de abajo hacia arriba. Quien lo dibuje
    solo tiene que recorrerlo en orden; ahi esta todo el apilado.
    """
    capas = []
    for c in estructura["capas"]:
        if c["clase"] == "group":
            continue
        d = {
            "id": c["id"],
            "nombre": c.get("nombre_limpio") or _limpiar_nombre(c["nombre"]),
            "nombre_psd": c["nombre"],
            "rol": c.get("rol", "decorativo"),
            "clase": c["clase"],
            "editable": bool(c.get("editable")),
            "variante": bool(c.get("_variante") or (not c["visible"])
                             or c.get("oculto_por_grupo")),
            "confianza": c.get("confianza", 0.0),
            "caja": c.get("caja_render") or c["caja"],
        }
        if c.get("sku"):
            d["sku"] = c["sku"]
        if c["clase"] == "type":
            d["texto"] = c.get("texto") or ""
            d["caja_texto"] = c["caja"]
            d["fuente"] = c.get("fuente")
            d["tamano"] = c.get("tamano")
            d["color"] = c.get("color")
        png = rasters.get(c["id"])
        if png:
            d["pixeles"] = "data:image/png;base64," + png
        capas.append(d)

    return {
        "formato": FORMATO,
        "version": FORMATO_VERSION,
        "origen": {"archivo": estructura["archivo"],
                   "modo": estructura["lienzo"]["modo"]},
        "lienzo": {"ancho": estructura["lienzo"]["ancho"],
                   "alto": estructura["lienzo"]["alto"]},
        # Los PNG vienen reducidos para que el paquete sea manejable. Quien lo
        # dibuje debe estirarlos a la caja, que SI va en pixeles del original.
        "escala_pixeles": estructura.get("escala_render", 1.0),
        "asistido_por_ia": estructura.get("asistido_por_ia", False),
        "capas": capas,
    }


def _capas_resolucion_real(ruta_psd: str | Path, estructura: dict) -> list[tuple]:
    """Las capas a tamano original, en orden, listas para compose_psd.

    Aparte del rasterizado del HTML porque ahi los pixeles van reducidos para
    que el archivo pese poco. Un PSD que sale a imprenta no admite eso.
    """
    from psd_tools import PSDImage

    psd = PSDImage.open(ruta_psd)
    por_ruta: dict[str, Any] = {}

    def recoger(grupo, camino):
        for capa in grupo:
            ruta = " / ".join(camino + [capa.name or ""])
            por_ruta[ruta] = capa
            if capa.is_group():
                recoger(capa, camino + [capa.name or ""])

    recoger(psd, [])

    salida = []
    for c in estructura["capas"]:
        if c["clase"] == "group" or c.get("_variante"):
            continue
        capa = por_ruta.get(c["ruta"])
        if capa is None:
            continue
        try:
            img = capa.composite()
        except Exception:
            img = None
        if img is None or img.width < 1:
            continue
        nombre = c.get("nombre_limpio") or c["nombre"]
        salida.append((nombre, img.convert("RGBA"),
                       int(capa.left), int(capa.top)))
    return salida


def a_psd(ruta_psd: str | Path, estructura: dict, salida: str | Path) -> None:
    """Un PSD nuevo con una capa por elemento y el nombre ya limpio.

    No es el PSD original: es el mismo contenido con la nomenclatura que
    decidio el convertidor, que es lo que lo vuelve manejable despues. Las
    capas van rasterizadas —ni texto editable ni vectores—, igual que hace el
    export del Visor.
    """
    from renderer import compose_psd

    capas = _capas_resolucion_real(ruta_psd, estructura)
    L = estructura["lienzo"]
    lienzo = (L["ancho"], L["alto"])
    vista = Image.new("RGBA", lienzo, (255, 255, 255, 255))
    for _n, img, x, y in capas:
        try:
            vista.alpha_composite(img, (max(0, x), max(0, y)))
        except ValueError:
            pass                                # capa que se sale del lienzo
    compose_psd(capas, lienzo, str(salida), preview=vista)


def a_pdf(ruta_psd: str | Path, estructura: dict, salida: str | Path) -> None:
    """La pieza aplanada en PDF, a la resolucion del PSD.

    Es un PDF de imagen, no de vectores: el texto va rasterizado y no se puede
    seleccionar ni buscar. Sirve para revisar y aprobar; para imprenta de
    verdad hace falta la salida con el texto en curvas.
    """
    from psd_tools import PSDImage

    img = PSDImage.open(ruta_psd).composite()
    if img is None:
        L = estructura["lienzo"]
        img = Image.new("RGB", (L["ancho"], L["alto"]), "white")
    img.convert("RGB").save(str(salida), "PDF", resolution=150.0)


def _esc(s: Any) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# ─── Todo junto ──────────────────────────────────────────────────────────────
def convertir(ruta_psd: str | Path, api_key: Optional[str] = None,
              nombre: Optional[str] = None) -> tuple[str, dict, dict]:
    """PSD -> (html, estructura, rasters). Los tres pasos en orden.

    `nombre` es el del archivo que subio la persona. Hace falta porque el
    servidor guarda la subida con un nombre propio, y sin esto la plantilla
    se acabaria llamando "origen.psd" en vez de "Cabecera 5 Central B".
    """
    estructura = analizar(ruta_psd)
    if nombre:
        estructura["archivo"] = nombre
    estructura = proponer_roles(estructura, api_key)
    rasters = rasterizar(ruta_psd, estructura)
    return a_html(estructura, rasters), estructura, rasters


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        raise SystemExit("uso: python convertidor.py <archivo.psd> [salida.html]")
    html, est, ras = convertir(sys.argv[1])
    destino = Path(sys.argv[2] if len(sys.argv) > 2 else "plantilla.html")
    destino.write_text(html, encoding="utf-8")
    destino.with_suffix(".json").write_text(
        json.dumps(est, indent=2, ensure_ascii=False), encoding="utf-8")
    editables = [c for c in est["capas"] if c.get("editable")]
    print(f"{destino}  ·  {len(editables)} huecos  ·  "
          f"IA: {'si' if est.get('asistido_por_ia') else 'no'}")
