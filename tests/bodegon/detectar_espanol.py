"""Busca texto en espanol en la interfaz, contra el ARCHIVO.

La app esta en ingles. Verificarlo abriendo la app y mirando el DOM NO
alcanza: es una sola pagina con secciones que aparecen por etapas (las
propuestas exigen subir productos, la descarga exige elegir una), asi que
un barrido sobre lo renderizado deja fuera media aplicacion. Paso cuatro
veces: aparecio espanol en propuestas, en el panel de IA, en el selector
de lotes y en la pantalla de descarga, y lo encontro el usuario.

Esto recorre el archivo entero — HTML, JS y los modulos Python — sin
depender de que nada este visible.

    .venv/bin/python tests/bodegon/detectar_espanol.py
"""
import os, sys
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

import re, sys, unicodedata

# Palabras españolas que NO existen en inglés. Sin ellas un detector por
# acentos se pierde "Crear", "Listo", "Mejorar plan", "Filas"...
ES = set("""
el la los las un una unos unas de del al y o pero si ni que qué quien quién como cómo cuando cuándo
donde dónde porque por para con sin sobre entre hasta desde según durante mediante tras ante bajo
es son era eran fue fueron ser estar está están estás estoy soy somos hay haber tiene tienen tienes
tener tuvo hace hacer haz hago haces puede pueden puedes poder debe deben debes deber va van vas ir
su sus mi mis tu tus nuestro nuestra nuestros nuestras este esta estos estas ese esa esos esas
aquel aquella todo toda todos todas otro otra otros otras mismo misma cada varios varias
crear crea creando creado subir sube subiendo subido abrir abre abierto guardar guarda guardado
volver vuelve elegir elige elegido generar genera generando generado descargar descarga descargado
mostrar muestra mostrado cargar carga cargando cargado borrar borra borrado quitar quita quitado
agregar agrega agregado buscar busca buscando aplicar aplica aplicado enviar envía enviado
producto productos pieza piezas proyecto proyectos plantilla plantillas estilo estilos ajuste ajustes
biblioteca fondo fondos fila filas sombra sombras profundidad imagen imágenes composición composiciones
tamaño tamaños ángulo ángulos separación espaciado usuario usuarios contraseña correo cuenta cuentas
listo lista mejorar mejora nuevo nueva reciente recientes activo activos dato datos marca marcas
aviso avisos momento momentos vez veces día días noche buenos buenas gracias
propuesta propuestas mecánica mecánicas promoción promociones bodegón bodegones lote lotes
carpeta carpetas archivo archivos capa capas nombre nombres orden página páginas pantalla
inicio principal siguiente anterior cerrar cierra sesión inténtalo espera pronto próximamente
válido válida inválido inválida requerido opcional obligatorio máximo mínimo primero última último
""".split())

def es_espanol(t):
    if re.search(r'[¿¡]', t): return True
    pal = [unicodedata.normalize('NFKD', w.lower()).encode('ascii','ignore').decode()
           for w in re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{2,}", t)]
    if not pal: return False
    esn = {unicodedata.normalize('NFKD', w).encode('ascii','ignore').decode() for w in ES}
    hits = sum(1 for w in pal if w in esn)
    return hits > 0

def revisar_html(ruta):
    s = open(ruta, encoding='utf-8').read()
    s = re.sub(r'<(script|style)\b[^>]*>.*?</\1>', lambda m: '\n'*m.group(0).count('\n'), s, flags=re.S)
    s = re.sub(r'<!--.*?-->', lambda m: '\n'*m.group(0).count('\n'), s, flags=re.S)
    out=[]
    for i, ln in enumerate(s.splitlines(), 1):
        for t in re.findall(r'>([^<>]{2,})<', ln):
            t=t.strip()
            if t and es_espanol(t): out.append((i, 'texto', t))
        for a in ('placeholder','title','alt','aria-label','label'):
            for t in re.findall(a+r'="([^"]{2,})"', ln):
                if es_espanol(t): out.append((i, a, t))
    return out

def revisar_js(ruta):
    s = open(ruta, encoding='utf-8').read()
    js = ''.join(m.group(1) for m in re.finditer(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', s, re.S))
    js = re.sub(r'//[^\n]*', '', js)
    js = re.sub(r'/\*.*?\*/', '', js, flags=re.S)
    out=[]
    for t in set(re.findall(r"""['"`]([^'"`\n]{3,120})['"`]""", js)):
        if re.match(r'^[\w\-#.\[\]/:?=&%${}]+$', t): continue      # selectores, rutas, clases
        if es_espanol(t): out.append((0, 'js', t))
    return out

def revisar_py(ruta):
    s = open(ruta, encoding='utf-8').read()
    s = re.sub(r'"""(?:.|\n)*?"""', '', s)                          # docstrings fuera
    out=[]
    for i, ln in enumerate(s.splitlines(), 1):
        cod = ln.split('#')[0]                                      # comentarios fuera
        for t in re.findall(r"""['"]([^'"\n]{4,140})['"]""", cod):
            if re.match(r'^[\w\-#./:?=&%{} ]+$', t) and not re.search(r'\s', t): continue
            if es_espanol(t): out.append((i, 'py', t))
    return out

tot=0
for etq, res in (('static/index.html — texto', revisar_html('static/index.html')),
                 ('static/index.html — JS',    revisar_js('static/index.html')),
                 ('main.py',                   revisar_py('main.py')),
                 ('renderer.py',               revisar_py('renderer.py')),
                 ('layout.py',                 revisar_py('layout.py')),
                 ('ai_proposal.py',            revisar_py('ai_proposal.py')),
                 ('image_gen.py',              revisar_py('image_gen.py'))):
    if res:
        print(f"\n══ {etq}: {len(res)}")
        for i,k,t in sorted(res)[:60]:
            print(f"   {str(i).rjust(5)} {k:11s} {t[:92]}")
        tot += len(res)
print(f"\n  TOTAL sospechoso: {tot}")
