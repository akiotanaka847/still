#!/usr/bin/env python3
"""Busca texto en espanol que se haya quedado en la interfaz.

Historia de por que esta escrito asi: las tres primeras versiones de este
script partian de una lista de palabras en espanol que yo escribia a mano, y
se le escaparon cosas ("Recordarme en este dispositivo", "Inclinado derecha",
"listos") simplemente porque no las habia anotado. Una lista hecha a mano solo
encuentra lo que ya sospechas.

Asi que ahora va al contrario: saca TODAS las palabras que el usuario puede
llegar a leer y marca las que NO estan en el diccionario ingles del sistema.
No hay que acertar con la palabra en espanol; hay que fallar en ingles. Lo que
queda son nombres propios y terminos tecnicos, y esos si caben en una lista
corta porque casi no cambian.
"""
import pathlib
import re
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[2]
DICCIONARIO = pathlib.Path("/usr/share/dict/words")

# Nombres propios, marcas y jerga tecnica que un diccionario ingles no trae.
PERMITIDAS = {
    # marca y producto
    "still", "ravel", "oxxo", "bodegon", "bodegones", "visora",
    # formatos y jerga
    "png", "psd", "jpg", "jpeg", "webp", "tif", "tiff", "bmp", "svg", "zip",
    "sku", "skus", "px", "json", "api", "url", "ui", "ux", "ai", "kb", "mb",
    "cta", "rgb", "cmyk", "dpi", "xl", "hd", "qa",
    # interfaz / web
    "auth", "login", "logout", "signin", "signup", "app", "dropdown",
    "checkbox", "tooltip", "placeholder", "subfolder", "subfolders",
    "dropzone", "thumbnail", "thumbnails", "preview", "presets", "preset",
    "toggle", "toggles", "topbar", "sidebar", "nav", "config", "params",
    "webkitdirectory", "multiple", "accept", "src", "href", "div", "span",
    # Identificadores de modo que van en la peticion al servidor. Estan en
    # espanol y ahi se quedan: son contrato con el backend y con las sesiones
    # ya guardadas. Lo que el usuario ve son las etiquetas ("Tilt right").
    "centrado", "uniforme", "dinamico", "sombra", "profundidad", "impacto",
    "inclinado", "izquierda", "derecha", "cascada", "libre", "avanzado",
    "piezas", "sueltos",
    # Variables locales: el codigo de este proyecto se escribe en espanol
    # a proposito, solo la interfaz va en ingles.
    "iniciales", "perfil", "sesion", "sesiones", "ruta", "fila", "filas",
    "ancho", "alto", "tope", "escala", "reparto", "aspecto", "orden",
    "miniatura", "miniaturas", "borrador", "borradores", "fotos", "origen",
    # ingles moderno que el diccionario de 1934 de macOS no conoce
    "plugin", "plugins", "promo", "promos",
    "email", "emails", "uploaded", "uploads", "uploading", "upload", "reset",
    "click", "clicks", "clicked", "online", "offline", "website", "workflow",
    "workfront", "adobe", "google", "microsoft", "dark", "lightweight",
    "backend", "frontend", "dashboard", "dashboards", "onboarding",
    "customizable", "resizable", "downloadable", "scalable", "sortable",
    "renders", "rendered", "rendering", "centered", "stepped", "tilted",
    "balanced", "generated", "generating", "settings", "credentials",
    "analyzes", "analyzing", "deliverables", "deliver", "delivers",
    "personalize", "prioritize", "optimized", "seconds", "minutes",
}


def cargar_diccionario() -> set[str]:
    if not DICCIONARIO.exists():
        print(f"falta {DICCIONARIO} — sin diccionario no puedo comparar", file=sys.stderr)
        sys.exit(2)
    return {p.strip().lower() for p in DICCIONARIO.read_text().splitlines() if p.strip()}


# El diccionario de macOS es de 1934: no trae "download", "pixels" ni
# "username". Antes de dar una palabra por sospechosa hay que intentar
# reducirla a algo que si conozca.
SUFIJOS = ("s", "es", "ed", "d", "ing", "ly", "er", "ers", "est", "n", "y")


def es_ingles(palabra: str, dic: set[str]) -> bool:
    if palabra in dic:
        return True
    for suf in SUFIJOS:
        if palabra.endswith(suf) and len(palabra) - len(suf) >= 3:
            raiz = palabra[: -len(suf)]
            # "carries" -> "carry", "running" -> "run"
            for cand in (raiz, raiz + "e", raiz + "y", raiz[:-1] if len(raiz) > 3 else raiz):
                if cand in dic:
                    return True
    # Compuestos pegados: username, download, workflow, backend.
    for i in range(3, len(palabra) - 2):
        if palabra[:i] in dic and palabra[i:] in dic:
            return True
    return False


# Clase cerrada: estas son practicamente todas, por eso escribirlas a mano si
# vale aqui (a diferencia de intentar listar sustantivos y verbos).
FUNCIONALES = {
    "el", "la", "los", "las", "un", "una", "unos", "unas", "lo", "al", "del",
    "de", "en", "con", "sin", "por", "para", "sobre", "entre", "hasta",
    "desde", "hacia", "segun", "tras", "durante", "ante", "bajo",
    "que", "qué", "quien", "cual", "cuando", "donde", "como", "porque",
    "pero", "aunque", "si", "no", "ni", "pues", "mientras",
    "yo", "tu", "usted", "nosotros", "ellos", "ellas", "me", "te", "se",
    "nos", "les", "su", "sus", "mi", "mis", "tus", "nuestro", "nuestra",
    "este", "esta", "esto", "estos", "estas", "ese", "esa", "eso", "esos",
    "esas", "aquel", "aquella", "todo", "toda", "todos", "todas", "otro",
    "otra", "otros", "otras", "mismo", "misma", "cada", "algun", "alguna",
    "ningun", "ninguna", "mucho", "mucha", "muchos", "muchas", "poco",
    "es", "son", "esta", "estan", "era", "eran", "fue", "fueron", "sea",
    "ser", "hay", "haber", "tiene", "tienen", "hace", "hacer", "puede",
    "pueden", "debe", "deben", "va", "van", "ya", "aqui", "ahora", "aun",
    "mas", "menos", "muy", "tambien", "solo", "asi", "bien", "cerca",
}

# Terminaciones que en ingles no aparecen.
SUFIJOS_ES = re.compile(
    r"(cion|ciones|miento|mientos|mente|ando|iendo|ados?|adas?|idos?|idas?"
    r"|eria|eza|dad|tad|aje|oso|osa|isim|aron|amos|emos|imos)$"
)

VOCAL_FINAL = re.compile(r"[aeiouns]$")


def parece_espanol(palabra: str, dic: set[str]) -> bool:
    baja = palabra.lower()
    if re.search(r"[áéíóúüñ¿¡]", palabra, re.I):
        return True
    if baja in FUNCIONALES:
        return True
    if SUFIJOS_ES.search(baja):
        return True
    # Ultimo filtro: no es ingles, es larga, y acaba como acaban las palabras
    # en espanol (a e i o u n s). Esto es lo que caza "listos" o "derecha".
    return len(baja) >= 5 and VOCAL_FINAL.search(baja) is not None and not es_ingles(baja, dic)


def texto_visible(html: str) -> list[tuple[int, str, str]]:
    """Devuelve (linea, origen, texto) de todo lo que el usuario puede leer.

    Cubre tres sitios, que son los tres por los que se me colo algo antes:
    el texto entre etiquetas, los atributos que se pintan en pantalla
    (placeholder, title, alt, value...) y las cadenas dentro del JavaScript.
    """
    fuera = []

    sin_comentarios = re.sub(r"<!--.*?-->", lambda m: "\n" * m.group().count("\n"), html, flags=re.S)
    # El CSS no lo lee nadie: fuera, o el informe se llena de rgba() y de
    # comentarios de hoja de estilo.
    sin_comentarios = re.sub(
        r"<style[^>]*>.*?</style>",
        lambda m: "\n" * m.group().count("\n"),
        sin_comentarios,
        flags=re.S,
    )

    # 1) JavaScript: solo cadenas literales, que es lo que acaba en pantalla.
    for m in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", sin_comentarios, re.S):
        base = sin_comentarios[: m.start()].count("\n") + 1
        cuerpo = m.group(1)
        # fuera comentarios del JS, que si van en espanol a proposito
        cuerpo = re.sub(r"//[^\n]*", "", cuerpo)
        cuerpo = re.sub(r"/\*.*?\*/", lambda x: "\n" * x.group().count("\n"), cuerpo, flags=re.S)
        for c in re.finditer(r"'([^'\\\n]{3,})'|\"([^\"\\\n]{3,})\"|`([^`\\]{3,})`", cuerpo):
            txt = c.group(1) or c.group(2) or c.group(3)
            # En una plantilla, lo que va en ${...} es codigo: fuera.
            txt = re.sub(r"\$\{[^}]*\}", " ", txt)
            fuera.append((base + cuerpo[: c.start()].count("\n"), "js", txt))
        # y el HTML que el JS inyecta queda cubierto por esas mismas cadenas
        sin_comentarios = sin_comentarios[: m.start()] + "\n" * cuerpo.count("\n") + sin_comentarios[m.end():]

    # 2) Atributos que se leen en pantalla.
    for m in re.finditer(
        r'\b(placeholder|title|alt|aria-label|value|data-tip)\s*=\s*"([^"]{3,})"',
        sin_comentarios,
    ):
        fuera.append((sin_comentarios[: m.start()].count("\n") + 1, m.group(1), m.group(2)))

    # 3) Texto entre etiquetas.
    plano = re.sub(r"<[^>]+>", "\n", sin_comentarios)
    for i, linea in enumerate(plano.split("\n"), 1):
        if linea.strip():
            fuera.append((i, "html", linea.strip()))

    return fuera


IDENTIFICADOR = re.compile(r"[A-Za-z0-9_$.\-]*[A-Za-z][A-Za-z0-9_$.\-]*")


def palabras_de_texto(txt: str) -> list[str]:
    """Palabras que de verdad lee una persona, sin identificadores de codigo.

    Los nombres de funcion y las claves internas (loadBgStyles, bod-bg-fname,
    profundidad_xl) salen en las mismas cadenas que el texto, y si no se
    apartan el informe se vuelve inservible. Se reconocen porque llevan
    guion, guion bajo, punto, digitos o mayuscula interna.
    """
    fuera = []
    for run in IDENTIFICADOR.findall(txt):
        if re.search(r"[_$.0-9\-]", run):
            continue
        if re.search(r"[a-z][A-Z]", run):  # camelCase
            continue
        fuera += re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{3,}", run)
    return fuera


def main() -> int:
    dic = cargar_diccionario()
    total = 0

    for rel in ["static/index.html"]:
        ruta = RAIZ / rel
        sospechas = []
        for linea, origen, txt in texto_visible(ruta.read_text()):
            for palabra in palabras_de_texto(txt):
                baja = palabra.lower()
                if baja in PERMITIDAS or es_ingles(baja, dic):
                    continue
                if not parece_espanol(palabra, dic):
                    continue
                sospechas.append((linea, origen, palabra, txt[:70]))

        print(f"\n══ {rel} — sospechosas: {len(sospechas)}")
        for linea, origen, palabra, ctx in sospechas:
            print(f"  {linea:>6} {origen:<12} {palabra:<18} {ctx}")
        total += len(sospechas)

    print(f"\n  TOTAL: {total}")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
