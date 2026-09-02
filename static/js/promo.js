/* =========================================================================
   SVG Template Demo — wizard logic
   Motor propio de plantillas: cada plantilla es un template.json (extraído
   automáticamente del PSD original vía psd-tools) + un PNG de fondo "limpio".
   El texto se renderiza con <text>/<tspan> nativos de SVG (no HTML/foreignObject
   — eso resultó "tainted" el canvas al exportar) — negrilla, tachado, superíndice
   y color por palabra son atributos nativos de SVG, medidos con canvas.measureText
   real para el ajuste de línea y de tamaño.
   ========================================================================= */

const templateSelect = document.getElementById("templateSelect");
const excelInput = document.getElementById("excelInput");
const excelStatus = document.getElementById("excelStatus");
const dataTableBody = document.querySelector("#dataTable tbody");
const noData = document.getElementById("noData");
const generateBtn = document.getElementById("generateBtn");
const generateBatchBtn = document.getElementById("generateBatchBtn");
const logEl = document.getElementById("log");
const previewWrap = document.getElementById("previewWrap");
const svgHost = document.getElementById("svgHost");
const downloadPngBtn = document.getElementById("downloadPngBtn");
const downloadSvgBtn = document.getElementById("downloadSvgBtn");
const openOutputBtn = document.getElementById("openOutputBtn");
const renderCanvas = document.getElementById("renderCanvas");
const batchPanel = document.getElementById("batchPanel");
const batchLogEl = document.getElementById("batchLog");
const batchTable = document.getElementById("batchTable");
const batchTableBody = document.querySelector("#batchTable tbody");
const batchTotal = document.getElementById("batchTotal");
const batchPsdChk = document.getElementById("batchPsdChk");

const state = {
  templates: [],
  template: null,
  excelFields: null,
  lastSvgString: null,
  lastPngBlob: null,
  lastCrop: null,        // tamaño real del recorte, necesario para armar el PSD
  lastMapping: null,     // campos ya resueltos, para rasterizar una capa por campo
  batchOutputs: [],      // [{name, png, svg}] del último lote, para bajar el ZIP
  templatesById: {}, // caché de plantillas completas (con fuentes/fondo ya resueltos) por id
};

function log(msg) {
  const time = new Date().toLocaleTimeString();
  logEl.textContent += `[${time}] ${msg}\n`;
  logEl.scrollTop = logEl.scrollHeight;
}

function normalize(text) {
  return String(text)
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^A-Za-z0-9]/g, "")
    .toUpperCase();
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

/* =========================================================================
   1. Cargar plantillas disponibles
   ========================================================================= */

async function loadTemplates() {
  const res = await fetch("/api/promo/templates");
  const json = await res.json();
  state.templates = json.templates;
  templateSelect.innerHTML = json.templates
    .map((t) => `<option value="${t.id}">${t.name} (${t.canvas.width}×${t.canvas.height})</option>`)
    .join("");
  if (json.templates.length) {
    await selectTemplate(json.templates[0].id);
  }
}

async function getTemplateById(id) {
  if (state.templatesById[id]) return state.templatesById[id];
  const res = await fetch(`/api/promo/templates/${id}`);
  const tpl = await res.json();
  tpl.id = id; // el endpoint individual no lo trae (solo el listado) — se necesita para comparar contra state.template.id
  await loadFontsIntoPage(tpl);
  state.templatesById[id] = tpl;
  return tpl;
}

const ORIENTATION_COLUMN_NAME = "ORIENTACION";

/** Lee la columna ORIENTACION del Excel (si viene) — "H"/"HORIZONTAL" o
 *  "V"/"VERTICAL" (case-insensitive, con o sin tilde, ya normalizado por el
 *  backend). Devuelve "horizontal" / "vertical" / null (si no viene la
 *  columna o está vacía — en ese caso se usa la plantilla elegida a mano en
 *  el desplegable, sin autoseleccionar nada). */
function resolveOrientationLetter(fields) {
  const f = (fields || []).find((x) => normalize(x.column) === normalize(ORIENTATION_COLUMN_NAME));
  if (!f || f.value === null || f.value === undefined) return null;
  const v = String(f.value).trim().toUpperCase();
  if (!v) return null;
  if (v.startsWith("H")) return "horizontal";
  if (v.startsWith("V")) return "vertical";
  return null;
}

/** Elige QUÉ plantilla completa usar para una fila del Excel: si la fila trae
 *  ORIENTACION (H/V), busca entre las plantillas disponibles la primera que
 *  matchee esa orientación (independiente de la elegida a mano en el
 *  desplegable) — así un mismo lote puede mezclar filas H y V. Si la fila no
 *  trae ORIENTACION, o no hay ninguna plantilla de esa orientación
 *  disponible, cae a la plantilla seleccionada manualmente. */
async function templateForRow(fields) {
  const orientation = resolveOrientationLetter(fields);
  if (orientation) {
    const match = state.templates.find((t) => (t.orientation || "vertical") === orientation);
    if (match) return await getTemplateById(match.id);
    log(`⚠ La fila pide orientación "${orientation}" pero no hay ninguna plantilla de ese tipo cargada — se usa la plantilla seleccionada en el desplegable.`);
  }
  if (!state.template) throw new Error("No hay plantilla seleccionada");
  return state.template;
}

async function selectTemplate(id) {
  state.template = await getTemplateById(id);
  log(`Plantilla cargada: ${state.template.name} (${state.template.canvas.width}×${state.template.canvas.height}px)`);
  log("Fuentes registradas para medición y render.");
}

/** Registra las fuentes incrustadas de la plantilla en document.fonts, para que
 *  tanto el canvas (measureText) como el DOM (medición de alto de párrafo) usen
 *  las métricas reales de Work Sans, no una fuente de reemplazo del sistema. */
async function loadFontsIntoPage(template) {
  // Una plantilla sin fuentes propias es un error de empaquetado, no un caso
  // válido: el texto sale con la tipografía de reemplazo del sistema y la
  // promo queda mal. Antes pasaba en silencio y el resultado dependía del
  // orden en que se tocaba el desplegable (si ya se había cargado otra
  // plantilla, sus fuentes quedaban registradas y esta las "heredaba").
  if (!Object.keys(template.fonts || {}).length) {
    log(`⚠ La plantilla "${template.name || template.id}" no trae fuentes propias — el texto puede salir con una tipografía incorrecta.`);
  }
  const loads = Object.entries(template.fonts || {}).map(([name, dataUrl]) => {
    const weight = weightForFontName(name);
    const face = new FontFace("Work Sans", `url(${dataUrl})`, { weight: String(weight) });
    document.fonts.add(face);
    return face.load();
  });
  await Promise.all(loads);
}

function weightForFontName(name) {
  if (/extrabold/i.test(name)) return 800;
  if (/black/i.test(name)) return 900;
  if (/semibold/i.test(name)) return 600;
  if (/bold/i.test(name)) return 700;
  return 400;
}

templateSelect.addEventListener("change", () => selectTemplate(templateSelect.value));

/* =========================================================================
   2. Leer Excel
   ========================================================================= */

excelInput.addEventListener("change", async () => {
  const file = excelInput.files[0];
  if (!file) return;
  excelStatus.textContent = "Leyendo...";
  excelStatus.className = "status";
  try {
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch("/api/promo/read-excel", { method: "POST", body: formData });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Error leyendo el Excel");
    }
    const json = await res.json();
    state.excelFields = json.fields;
    renderDataFound(json.fields);
    excelStatus.textContent = "✓ Cargado";
    excelStatus.className = "status ok";
    generateBtn.disabled = false;
    generateBatchBtn.disabled = false;
    log(`Excel leído: ${json.fields.length} campos encontrados (hoja "${json.sheet}").`);
  } catch (e) {
    excelStatus.textContent = "✗ Error";
    excelStatus.className = "status err";
    log(`ERROR leyendo Excel: ${e.message}`);
  }
});

function renderDataFound(fields) {
  dataTableBody.innerHTML = "";
  if (!fields.length) { noData.style.display = "block"; return; }
  noData.style.display = "none";
  for (const f of fields) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td><strong>${escapeHtml(f.column)}</strong></td>
                    <td>${escapeHtml(f.value)}</td>
                    <td>${f.type === "image" ? "🖼 imagen" : "🔤 texto"}</td>`;
    dataTableBody.appendChild(tr);
  }
}

/* =========================================================================
   3. Mapear campos del Excel a campos de la plantilla (por nombre de columna)
   ========================================================================= */

function computeMapping(excelFields, templateFields) {
  return templateFields.map((tf) => {
    const norm = normalize(tf.excel_column);
    const match = excelFields.find((f) => normalize(f.column) === norm);
    return {
      ...tf,
      matched: !!match,
      value: match ? match.value : null,
      runs: match ? match.runs : null,
      price: match ? match.price : null,
    };
  });
}

/* =========================================================================
   4. Construir el SVG
   ========================================================================= */

/** Fuerza el preserveAspectRatio con el que se interpreta un SVG dinámico
 *  (CALLOUT/BRAND) DENTRO de su propio documento, escribiéndolo en la
 *  etiqueta <svg> raíz. Es necesario por un comportamiento real de Chrome
 *  (confirmado con los assets reales del usuario, no es teórico): cuando un
 *  <image> exterior referencia OTRO SVG como contenido (no un PNG), Chrome
 *  NO usa el preserveAspectRatio del <image> exterior para decidir la
 *  ALINEACIÓN del contenido — usa el preserveAspectRatio del propio SVG
 *  referenciado, que por defecto es "xMidYMid meet" (centrado) si el
 *  archivo no lo especifica (como pasa con los exports de Illustrator que
 *  usamos, que solo traen viewBox). Por eso el callout alineado a la
 *  derecha ("xMaxYMid meet" en el <image> que lo dibuja) se veía
 *  CENTRADO en la práctica — con una caja mucho más ancha que el contenido
 *  real (la caja crece de forma simétrica en scaleMapping), ese centrado
 *  corría el contenido bien a la izquierda de donde debía, y el recorte
 *  final del lienzo (por la izquierda) terminaba cortando texto legible
 *  ("NUEVO" quedaba como "EVO"). La cuenta de dónde "debería" caer el
 *  contenido (asumiendo que el <image> exterior sí controla la alineación)
 *  daba bien — el bug estaba en esa suposición, no en la aritmética.
 *  Solución verificada en aislado (con y sin recorte): escribir el
 *  preserveAspectRatio deseado directamente en el <svg> del archivo
 *  referenciado hace que Chrome sí lo respete. */
function withInnerPreserveAspectRatio(dataUrl, preserveAspectRatio) {
  if (!dataUrl || !dataUrl.startsWith("data:image/svg+xml")) return dataUrl;
  try {
    const commaIdx = dataUrl.indexOf(",");
    if (commaIdx === -1) return dataUrl;
    const meta = dataUrl.slice(5, commaIdx); // ej. "image/svg+xml;base64" o "image/svg+xml"
    const payload = dataUrl.slice(commaIdx + 1);
    const svgText = meta.includes("base64") ? atob(payload) : decodeURIComponent(payload);
    const openTagMatch = svgText.match(/<svg\b[^>]*>/i);
    if (!openTagMatch) return dataUrl;
    let openTag = openTagMatch[0];
    if (/\bpreserveAspectRatio="[^"]*"/i.test(openTag)) {
      openTag = openTag.replace(/\bpreserveAspectRatio="[^"]*"/i, `preserveAspectRatio="${preserveAspectRatio}"`);
    } else {
      openTag = openTag.replace(/<svg\b/i, `<svg preserveAspectRatio="${preserveAspectRatio}"`);
    }
    const patchedSvgText = svgText.slice(0, openTagMatch.index) + openTag + svgText.slice(openTagMatch.index + openTagMatch[0].length);
    // Re-codificar siempre como URI-encoded (no base64) — más simple y evita
    // problemas de btoa() con caracteres fuera de Latin1 si el SVG original
    // tenía texto/metadata en UTF-8.
    return `data:image/svg+xml,${encodeURIComponent(patchedSvgText)}`;
  } catch (e) {
    return dataUrl; // si algo falla al parsear, mejor seguir con el original que romper la generación
  }
}

async function fetchAssetDataUrl(filename, preserveAspectRatio = "xMidYMid meet") {
  const res = await fetch(`/api/promo/asset?filename=${encodeURIComponent(filename)}`);
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || `No se pudo resolver el asset ${filename}`);
  }
  const json = await res.json();
  return withInnerPreserveAspectRatio(json.data_url, preserveAspectRatio);
}

const ERROR_ASSET_FILENAME = "CA_ERROR.svg";

/** Para assets DINÁMICOS (los que vienen del Excel: CALLOUT, BRAND) — si el
 *  nombre de archivo que indica el Excel no existe en assets/, en vez de
 *  fallar se usa el asset fijo de error como respaldo, avisando en el log.
 *  No confundir con "la celda viene vacía" (eso ya se salta antes de llegar
 *  acá, vía isPresent — nunca dispara esta función).
 *  `preserveAspectRatio` DEBE coincidir con el que se va a usar en el
 *  <image> que dibuja este asset (ver withInnerPreserveAspectRatio) — por
 *  defecto "xMidYMid meet" (centrado), que es la mayoría de los casos; el
 *  callout alineado a la derecha en horizontal pasa "xMaxYMid meet". */
async function fetchDynamicAssetDataUrl(filename, preserveAspectRatio = "xMidYMid meet") {
  try {
    return await fetchAssetDataUrl(filename, preserveAspectRatio);
  } catch (e) {
    log(`⚠ No se encontró el asset "${filename}" indicado en el Excel — usando ${ERROR_ASSET_FILENAME} de respaldo.`);
    return await fetchAssetDataUrl(ERROR_ASSET_FILENAME, preserveAspectRatio);
  }
}

function escapeXml(str) {
  return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

const FONT_FAMILY_CSS = "Work Sans";
const LINE_HEIGHT_RATIO = 1.15;
const ASCENT_RATIO = 0.8; // aproximación de línea base desde el top de la caja (texto sin tildes en mayúscula)
const ASCENT_RATIO_ACCENTED = 0.92; // igual, pero con margen extra para tildes en mayúsculas (Á É Í Ó Ú Ñ Ü), que suben más arriba que una letra sin acento — si no se detectan, no se reserva de más (mismo criterio que con los descendentes, para no romper el espaciado parejo entre elementos)

function ascentRatioFor(text) {
  return /[ÁÉÍÓÚÑÜáéíóúñü]/.test(text || "") ? ASCENT_RATIO_ACCENTED : ASCENT_RATIO;
}

/** Mide el ancho real de un texto con una fuente/peso/tamaño dados, usando un
 *  canvas 2D oculto — requiere que la fuente ya esté registrada en document.fonts
 *  (ver loadFontsIntoPage), si no mide con una fuente de reemplazo y sale mal. */
function measureTextWidth(text, weight, fontSizePx) {
  const canvas = measureTextWidth._canvas || (measureTextWidth._canvas = document.createElement("canvas"));
  const ctx = canvas.getContext("2d");
  ctx.font = `${weight} ${fontSizePx}px '${FONT_FAMILY_CSS}'`;
  return ctx.measureText(text).width;
}

/** Reduce el tamaño de fuente hasta que el texto (una sola línea) quepa dentro de
 *  maxWidth. No sube de tamaño si ya cabe — solo reduce cuando es necesario. */
function fitFontSizeToWidth(text, weight, baseFontSize, maxWidth) {
  const w = measureTextWidth(text, weight, baseFontSize);
  if (w <= maxWidth) return baseFontSize;
  return baseFontSize * (maxWidth / w);
}

/** Parte los runs en palabras individuales (conservando el estilo de cada una),
 *  marcando con `_spaceAfter` si en el texto ORIGINAL había un espacio después de
 *  esa palabra — así "Ruffles®" (sin espacio, aunque "®" sea su propio run por el
 *  superíndice automático) no se separa con un espacio de más al armar el SVG. */
function runsToWords(runs) {
  const words = [];
  for (const r of runs) {
    const tokens = r.text.match(/\s+|\S+/g) || [];
    for (const t of tokens) {
      if (/^\s+$/.test(t)) {
        if (words.length) words[words.length - 1]._spaceAfter = true;
      } else {
        words.push({ ...r, text: t, _spaceAfter: false });
      }
    }
  }
  return words;
}

/** Reparte palabras en líneas según el ancho real medido (canvas.measureText),
 *  respetando el estilo de cada palabra (negrilla/superíndice miden distinto) y
 *  solo agregando espacio entre dos palabras si realmente lo había entre ellas. */
function wrapWordsIntoLines(words, weight, fontSizePx, maxWidth) {
  const spaceWidth = measureTextWidth(" ", weight, fontSizePx);
  const lines = [[]];
  let currentWidth = 0;
  for (const w of words) {
    const wSize = (w.superscript || w.subscript) ? fontSizePx * 0.6 : fontSizePx;
    const wWeight = w.bold ? Math.max(weight, 700) : weight;
    const wWidth = measureTextWidth(w.text, wWeight, wSize);
    const prevWord = lines[lines.length - 1][lines[lines.length - 1].length - 1];
    const extra = prevWord && prevWord._spaceAfter ? spaceWidth : 0;
    if (currentWidth + extra + wWidth > maxWidth && lines[lines.length - 1].length > 0) {
      lines.push([]);
      currentWidth = 0;
    }
    const isFirstInLine = lines[lines.length - 1].length === 0;
    const prevInLine = isFirstInLine ? null : lines[lines.length - 1][lines[lines.length - 1].length - 1];
    lines[lines.length - 1].push(w);
    currentWidth += (prevInLine && prevInLine._spaceAfter ? spaceWidth : 0) + wWidth;
  }
  return lines.filter((l) => l.length > 0);
}

/** Ancho real de una línea ya armada (suma de sus palabras + solo los espacios
 *  que realmente existían en el texto original). */
function measureLineWidth(line, weight, fontSizePx) {
  const spaceWidth = measureTextWidth(" ", weight, fontSizePx);
  let w = 0;
  line.forEach((word, i) => {
    const wSize = (word.superscript || word.subscript) ? fontSizePx * 0.6 : fontSizePx;
    const wWeight = word.bold ? Math.max(weight, 700) : weight;
    w += measureTextWidth(word.text, wWeight, wSize);
    if (i > 0 && line[i - 1]._spaceAfter) w += spaceWidth;
  });
  return w;
}

/** Ajusta un párrafo para que quepa SIEMPRE en, como máximo, `maxLines` líneas
 *  (regla dura, sin excepción) — si el texto es muy largo, el tamaño de fuente
 *  se reduce todo lo que haga falta (sin un piso arbitrario), en vez de rendirse
 *  a un tamaño mínimo y dejar que el texto se desborde y empuje lo que va después
 *  (como el logo de marca) fuera de la plantilla. Devuelve las líneas ya
 *  armadas, el tamaño final, el alto real usado y el ancho de la línea más ancha
 *  (para el recorte final al contenido).
 */
function fitParagraph(runs, weight, baseFontSize, maxWidth, maxHeight, maxLines = 4) {
  const words = runsToWords(runs);
  const plainText = words.map((w) => w.text).join(" ");
  let fontSize = baseFontSize;
  const absoluteMinFontSize = 20; // salvaguarda de seguridad, no debería alcanzarse en la práctica
  let lines = wrapWordsIntoLines(words, weight, fontSize, maxWidth);

  // 1. Prioridad: nunca más de `maxLines` líneas, sin importar cuánto haya que reducir.
  while (lines.length > maxLines && fontSize > absoluteMinFontSize) {
    fontSize -= baseFontSize * 0.02;
    lines = wrapWordsIntoLines(words, weight, fontSize, maxWidth);
  }

  // 2. Salvaguarda adicional por si, aun con ≤maxLines, se excede el alto máximo
  //    disponible (caso extremo, ej. una sola palabra imposible de partir).
  let height = paragraphHeight(lines.length, fontSize, plainText);
  while (height > maxHeight && fontSize > absoluteMinFontSize) {
    fontSize -= baseFontSize * 0.02;
    lines = wrapWordsIntoLines(words, weight, fontSize, maxWidth);
    height = paragraphHeight(lines.length, fontSize, plainText);
  }

  const maxLineWidth = Math.max(...lines.map((l) => measureLineWidth(l, weight, fontSize)));
  return { fontSize, lines, actualHeight: height, maxLineWidth };
}

/** Alto de un bloque de texto. El colchón para "descendentes" (la parte de
 *  letras como g/j/p/q/y que baja de la línea base) solo se reserva si el texto
 *  realmente tiene esas letras — si no, el colchón es mínimo. Esto evita que
 *  elementos con fuentes MUY grandes (como el precio, con dígitos que no bajan de
 *  la línea base) generen un espacio muerto desproporcionado comparado con
 *  elementos de fuente más chica, lo que rompía la sensación de espaciado parejo
 *  entre elementos aunque el mismo "gap" se aplicara en el código.
 */
function paragraphHeight(lineCount, fontSizePx, text) {
  const hasDescenders = /[gjpqy]/.test(text || "");
  const descentRatio = hasDescenders ? 0.22 : 0.03;
  return fontSizePx * ascentRatioFor(text) + (lineCount - 1) * fontSizePx * LINE_HEIGHT_RATIO + fontSizePx * descentRatio;
}

/** Arma un <text text-anchor="middle"> con un <tspan> por palabra — negrilla,
 *  tachado, superíndice y color por palabra son atributos NATIVOS de SVG, sin
 *  necesitar characterStyleRanges ni ninguna API frágil. text-anchor="middle"
 *  centra automáticamente todo el contenido de la línea (aunque tenga varios
 *  estilos mezclados) alrededor de centerX. Solo se agrega un espacio después de
 *  una palabra si `_spaceAfter` lo marca (es decir, si realmente había un
 *  espacio ahí en el texto original) — así "Ruffles®" no se separa por el hecho
 *  de que "®" sea su propio run (por el superíndice automático). */
function lineToSvgText(line, weight, fontSizePx, color, centerX, baselineY) {
  const tspans = line.map((w) => {
    const text = escapeXml(w.text) + (w._spaceAfter ? " " : "");
    const wWeight = w.bold ? Math.max(weight, 700) : weight;
    let attrs = ` font-weight="${wWeight}"`;
    if (w.italic) attrs += ` font-style="italic"`;
    if (w.strike) attrs += ` text-decoration="line-through"`;
    if (w.superscript) attrs += ` baseline-shift="super" font-size="${(fontSizePx * 0.6).toFixed(1)}"`;
    if (w.subscript) attrs += ` baseline-shift="sub" font-size="${(fontSizePx * 0.6).toFixed(1)}"`;
    if (w.color) attrs += ` fill="${w.color}"`;
    return `<tspan${attrs}>${text}</tspan>`;
  }).join("");
  return `<text x="${centerX}" y="${baselineY}" text-anchor="middle" font-family="${FONT_FAMILY_CSS}" font-size="${fontSizePx}" fill="${color}">${tspans}</text>`;
}

/** Escala font_size/box (y prefix.font_size) de cada campo del mapping por
 *  `scale` — usado para "agrandar todo" en la segunda pasada de
 *  buildSvgWithMaxFill. Los campos sintéticos (PESOS, doble_desc, etc.) no
 *  tienen box/font_size propios así que pasan sin tocar.
 *  `priceBoost` (opcional) aplica un impulso EXTRA solo al campo PESOS — el
 *  precio/porcentaje es el gancho visual más fuerte de este tipo de
 *  plantilla, así que cuando sobra espacio debería crecer más que el resto,
 *  no exactamente igual que todo lo demás. */
function scaleMapping(mapping, scale, priceBoost = 1) {
  if (scale === 1 && priceBoost === 1) return mapping;
  return mapping.map((f) => {
    const scaled = { ...f };
    const extra = f.excel_column === "PESOS" ? priceBoost : 1;
    if (typeof scaled.font_size === "number") scaled.font_size = scaled.font_size * scale * extra;
    if (scaled.box) {
      // OJO: "x" es una posición ABSOLUTA (no un tamaño) — multiplicarla por
      // "scale" corre el centro del elemento, porque el ancho del LIENZO no
      // se escala, solo el contenido. Hay que mantener el mismo centro
      // horizontal original y solo crecer el ancho/alto simétricamente
      // alrededor de ese centro (así CALLOUT/BRAND se agrandan sin
      // descentrarse). "y" sí se puede escalar tal cual porque solo se usa en
      // una DIFERENCIA (el cálculo del "gap"), no como posición absoluta.
      const originalCenterX = scaled.box.x + scaled.box.width / 2;
      const newWidth = scaled.box.width * scale * extra;
      const newHeight = scaled.box.height * scale * extra;
      scaled.box = {
        x: originalCenterX - newWidth / 2,
        y: scaled.box.y * scale,
        width: newWidth,
        height: newHeight,
      };
    }
    if (scaled.prefix) {
      scaled.prefix = { ...scaled.prefix, font_size: scaled.prefix.font_size * scale * extra };
    }
    return scaled;
  });
}

/** Regla general: el conjunto de la promo nunca debe cortarse por alto o
 *  ancho, PERO tampoco debe desperdiciar espacio de sobra cuando faltan
 *  elementos opcionales (CALLOUT/FRASE APOYO/BRAND) o la descripción usa
 *  menos líneas de las que tiene permitidas — en esos casos, todo el
 *  contenido debe crecer para aprovechar mejor el espacio vertical
 *  disponible, sin exceder el ancho de la plantilla. Además, el precio/
 *  porcentaje (el "gancho" de la promo) tiene PRIORIDAD para crecer más que
 *  el resto cuando hay margen.
 *
 *  Estrategia de varias pasadas (reutiliza buildSvg tal cual, sin tocar sus
 *  fórmulas internas — solo le pasamos números de entrada más grandes):
 *   1. Pasada 1: tamaños de diseño normales -> mide cuánto ocupa el
 *      contenido real (crop.width/crop.height).
 *   2. Calcular el factor de escala uniforme más grande que sigue cabiendo
 *      en AMBAS dimensiones ("contain fit" del conjunto completo).
 *   3. Intentar una pasada con ESE factor + un impulso extra solo al precio.
 *      Si el resultado sigue cabiendo en el lienzo, usarlo.
 *   4. Si el impulso extra hizo que algo se pasara, caer de nuevo al
 *      crecimiento uniforme (sin impulso) — nunca se permite exceder el
 *      ancho/alto de la plantilla, eso es innegociable.
 */
/** Dibuja el bloque de PESOS (precio/porcentaje/cantidad/combinado/doble,
 *  en cualquiera de sus modos) centrado en `centerX`, ajustado a `maxWidth`,
 *  empezando en `startY`. Devuelve la nueva posición Y (startY + alto real
 *  usado). Extraído como función compartida (antes vivía inline dentro del
 *  flujo de la vertical) para que TANTO buildSvg (vertical: centerX=cw/2,
 *  maxWidth=cw*0.9, un solo cursor) COMO buildSvgHorizontal (centerX/maxWidth
 *  de la caja de PESOS únicamente, cursor propio de su columna) dibujen el
 *  precio con exactamente la misma lógica — sin duplicar ~370 líneas de
 *  reglas por modo entre los dos motores. */
function renderPesosBlock(pesos, weight, prefixWeight, centerX, maxWidth, startY, bodyParts, trackX, trackY) {
  let cursorY = startY;
    if (pesos.price && pesos.price.mode === "percent") {
      // --- Modo porcentaje: número grande + "%"/"desc" apilados (mismo patrón
      // que centavos/c-u) + opcionalmente "c/u" aparte, alineado arriba con el
      // "%" (igual línea base que "%", no centrado contra el número grande). ---
      const numberText = pesos.price.number;
      const mainMaxFontSize = pesos.font_size;
      const percentFontSize = pesos.font_size * 0.5;
      // "desc" se escala para medir EXACTAMENTE el mismo ancho que "%" (no un
      // porcentaje fijo del tamaño de "%", que le daba un ancho distinto).
      const percentWidthAtBase = measureTextWidth("%", weight, percentFontSize);
      const descWidthAtPercentSize = measureTextWidth("desc", weight, percentFontSize);
      const descFontSize = percentFontSize * (percentWidthAtBase / descWidthAtPercentSize);
      const cuText = pesos.price.has_cu ? "c/u" : null;
      const cuFontSize = percentFontSize * 0.7;
      const descColor = "rgb(0,0,0)"; // "desc" siempre en negro, no del color del precio
      // Color explícito de la celda DESCUENTO en Excel (si lo hay) pisa el
      // color fijo de la plantilla — igual criterio que el resto de campos.
      const mainColor = pesos.price.color || pesos.color;

      const gapBase = pesos.font_size * 0.06;
      const mainWidthBase = measureTextWidth(numberText, weight, pesos.font_size);
      const stackWidthBase = percentWidthAtBase; // "%" y "desc" ya miden lo mismo
      const cuWidthBase = cuText ? measureTextWidth(cuText, weight, cuFontSize) : 0;
      const totalWidthBase = mainWidthBase + gapBase + stackWidthBase + (cuText ? gapBase + cuWidthBase : 0);
      const maxPesosWidth = maxWidth;
      const scale = totalWidthBase > maxPesosWidth ? maxPesosWidth / totalWidthBase : 1;

      const mainWidth = mainWidthBase * scale;
      const stackWidth = stackWidthBase * scale;
      const cuWidth = cuWidthBase * scale;
      const gap = gapBase * scale;
      const scaledMainMaxFontSize = mainMaxFontSize * scale;
      const scaledPercentFontSize = percentFontSize * scale;
      const scaledDescFontSize = descFontSize * scale;
      const scaledCuFontSize = cuFontSize * scale;
      const totalWidth = mainWidth + gap + stackWidth + (cuText ? gap + cuWidth : 0);

      const mainCenterX = centerX - totalWidth / 2 + mainWidth / 2;
      const stackCenterX = mainCenterX + mainWidth / 2 + gap + stackWidth / 2;
      const stackLeftX = stackCenterX - stackWidth / 2;
      const cuCenterX = stackCenterX + stackWidth / 2 + gap + cuWidth / 2;

      const baselineY = cursorY + scaledMainMaxFontSize * ascentRatioFor(numberText);

      bodyParts.push(`<text x="${mainCenterX}" y="${baselineY}" text-anchor="middle" font-family="${FONT_FAMILY_CSS}" font-weight="${weight}" font-size="${scaledMainMaxFontSize.toFixed(1)}" fill="${mainColor}">${escapeXml(numberText)}</text>`);

      // "%" arriba y "desc" abajo — siempre los dos juntos, "desc" es fijo (no
      // viene del Excel, aparece siempre que el valor sea un %), sin negrilla,
      // y con un margen chico entre ambos (no una fórmula fija que dejaba mucho
      // espacio vacío entre el "%" y "desc").
      const descCapHeight = scaledDescFontSize * 0.72;
      const smallGap = scaledMainMaxFontSize * 0.03;
      const percentBaselineY = baselineY - descCapHeight - smallGap;
      const descWeight = 400; // "desc" sin negrilla
      bodyParts.push(`<text x="${stackLeftX}" y="${percentBaselineY}" text-anchor="start" font-family="${FONT_FAMILY_CSS}" font-weight="${weight}" font-size="${scaledPercentFontSize.toFixed(1)}" fill="${mainColor}">%</text>`);
      bodyParts.push(`<text x="${stackLeftX}" y="${baselineY}" text-anchor="start" font-family="${FONT_FAMILY_CSS}" font-weight="${descWeight}" font-size="${scaledDescFontSize.toFixed(1)}" fill="${descColor}">desc</text>`);

      // "c/u" opcional, alineado ARRIBA con el "%" — misma línea base que "%",
      // no centrado contra el número grande.
      if (cuText) {
        bodyParts.push(`<text x="${cuCenterX}" y="${percentBaselineY}" text-anchor="middle" font-family="${FONT_FAMILY_CSS}" font-weight="${weight}" font-size="${scaledCuFontSize.toFixed(1)}" fill="${mainColor}">c/u</text>`);
      }

      const lineHeight = paragraphHeight(1, scaledMainMaxFontSize, numberText);
      trackX(mainCenterX - mainWidth / 2, cuText ? cuCenterX + cuWidth / 2 : stackCenterX + stackWidth / 2);
      trackY(cursorY, cursorY + lineHeight);
      cursorY += lineHeight;
    } else if (pesos.price && pesos.price.mode === "double") {
      // --- Modo doble: dos líneas "Nx$PPPP" apiladas (promo "doble", dos
      // unidades por dos precios). Mismas reglas que el modo combinado ("x"
      // minúscula, "$" chico y centrado, multiplicador en negro/precio en
      // rojo) pero repetidas en 2 líneas independientes. Tamaño más reducido
      // que el combinado de una sola promo, porque aquí hay el doble de
      // contenido apilado.
      //
      // Si viene un CONECTOR ("O"/"o"/"Ó"/"ó", detectado en DESCRIPCION y
      // limpiado de ahí por el backend): se agrega una "ó" chica al inicio de
      // la SEGUNDA línea únicamente. La línea 1 mantiene su tamaño natural sin
      // sacrificar nada; solo la línea 2 se reduce lo que haga falta para que
      // quepa junto con la "ó" — por eso, con conector, cada línea puede
      // terminar con un tamaño de fuente distinto (sin conector, comparten el
      // mismo tamaño, como antes). ---
      const blackColor = "rgb(0,0,0)";
      const dollarText = pesos.prefix ? pesos.prefix.text : "$";
      const DOUBLE_SIZE_FACTOR = 0.5;
      const mainSize = pesos.font_size * DOUBLE_SIZE_FACTOR;
      const dollarRatio = pesos.prefix ? pesos.prefix.font_size / pesos.font_size : 0.47;
      const dollarSize = mainSize * dollarRatio;
      const maxWidthTarget = maxWidth;
      const TIGHT_LINE_RATIO = 0.95;
      const connectorText = pesos.price.connector || null;
      const connectorSize = dollarSize * 1.3; // 30% más grande que el "$"

      function measureLine(line, includeConnector) {
        let w = measureTextWidth(`${line.multiplier}x`, weight, mainSize)
          + measureTextWidth(dollarText, weight, dollarSize)
          + measureTextWidth(line.integer, weight, mainSize);
        if (includeConnector) w += measureTextWidth(connectorText, weight, connectorSize);
        return w;
      }

      const line1 = pesos.price.line1, line2 = pesos.price.line2;
      const width1Base = measureLine(line1, false);
      const width2Base = measureLine(line2, !!connectorText);
      const scale1 = width1Base > maxWidthTarget ? maxWidthTarget / width1Base : 1;
      const scale2 = width2Base > maxWidthTarget ? maxWidthTarget / width2Base : 1;

      // Sin conector: mismo tamaño para ambas líneas (como antes). Con
      // conector: cada línea se ajusta de forma independiente — la línea 1 no
      // sacrifica tamaño por culpa de la "ó" que solo afecta a la línea 2.
      const scaleLine1 = connectorText ? scale1 : Math.min(scale1, scale2);
      const scaleLine2 = connectorText ? scale2 : Math.min(scale1, scale2);

      const scaledMain1 = mainSize * scaleLine1, scaledDollar1 = dollarSize * scaleLine1;
      const scaledMain2 = mainSize * scaleLine2, scaledDollar2 = dollarSize * scaleLine2;
      const scaledConnector = connectorSize * scaleLine2;
      const width1 = width1Base * scaleLine1;
      const width2 = width2Base * scaleLine2;
      // El espacio entre líneas se basa en el tamaño de la línea 1 (la que no
      // se sacrifica), para que la separación se sienta consistente aunque la
      // línea 2 sea más chica por el conector.
      const lineHeightPer = scaledMain1 * TIGHT_LINE_RATIO;

      function pushLine(line, baselineY, scaledMain, scaledDollar, prefixConnector) {
        // Color explícito de las celdas MULTIPLICADOR/PESOS O PRECIO (o sus
        // variantes "2" en la línea 2) en Excel, si lo hay, pisa el negro/
        // color de plantilla fijos — cada línea toma el color de SUS propias
        // celdas de origen.
        const lineMultiplierColor = line.multiplier_color || blackColor;
        const linePriceColor = line.color || pesos.color;
        const shift = ((scaledMain - scaledDollar) * 0.72) / 2;
        let tspans = "";
        if (prefixConnector) {
          const connectorShift = ((scaledMain - scaledConnector) * 0.72) / 2;
          tspans += `<tspan fill="${blackColor}" font-size="${scaledConnector.toFixed(1)}" baseline-shift="${connectorShift.toFixed(1)}">${escapeXml(connectorText)}</tspan>`;
        }
        tspans +=
          `<tspan fill="${lineMultiplierColor}" font-size="${scaledMain.toFixed(1)}">${escapeXml(line.multiplier)}x</tspan>` +
          `<tspan fill="${linePriceColor}" font-size="${scaledDollar.toFixed(1)}" baseline-shift="${shift.toFixed(1)}">${escapeXml(dollarText)}</tspan>` +
          `<tspan fill="${linePriceColor}" font-size="${scaledMain.toFixed(1)}">${escapeXml(line.integer)}</tspan>`;
        bodyParts.push(`<text x="${centerX}" y="${baselineY}" text-anchor="middle" font-family="${FONT_FAMILY_CSS}" font-weight="${weight}">${tspans}</text>`);
      }

      const line1BaselineY = cursorY + scaledMain1 * ascentRatioFor(`${line1.multiplier}x${line1.integer}`);
      const line2BaselineY = line1BaselineY + lineHeightPer;
      pushLine(line1, line1BaselineY, scaledMain1, scaledDollar1, false);
      pushLine(line2, line2BaselineY, scaledMain2, scaledDollar2, !!connectorText);

      const totalHeight = scaledMain1 * ascentRatioFor("") + lineHeightPer + Math.max(scaledMain1, scaledMain2) * 0.05;
      trackX(centerX - (Math.max(width1, width2)) / 2, centerX + (Math.max(width1, width2)) / 2);
      trackY(cursorY, cursorY + totalHeight);
      cursorY += totalHeight;
    } else if (pesos.price && pesos.price.mode === "combined") {
      // --- Modo combinado: "2x$453" (multiplicador + "x" en negro, "$"+precio
      // en rojo). Si el precio queda en 2 cifras o menos, todo va en una sola
      // línea horizontal; si queda en 3 cifras, se apila en dos líneas
      // (multiplicador+x arriba, $+precio abajo), ambas centradas.
      //
      // Todo el conjunto usa un tamaño REDUCIDO respecto al precio "solo" (que
      // ocupa toda la plantilla) — con 2 números grandes + "x" + "$" + hasta 3
      // cifras de precio, a tamaño completo se desborda y empuja lo que viene
      // después (descripción, marca) fuera de la plantilla. Además, el "$" usa
      // su tamaño chico proporcional (igual que en el modo precio normal), no
      // el tamaño completo de los números. ---
      const multiplierText = pesos.price.multiplier;
      const priceIntegerText = pesos.price.integer;
      const blackColor = "rgb(0,0,0)";
      const dollarText = pesos.prefix ? pesos.prefix.text : "$";
      const isVertical = priceIntegerText.length >= 3;
      // Color explícito de MULTIPLICADOR/PESOS O PRECIO en Excel, si lo hay,
      // pisa el negro/color de plantilla fijos.
      const multiplierColor = pesos.price.multiplier_color || blackColor;
      const priceColor = pesos.price.color || pesos.color;

      const COMBINED_SIZE_FACTOR = 0.72;
      const mainSize = pesos.font_size * COMBINED_SIZE_FACTOR;
      const dollarRatio = pesos.prefix ? pesos.prefix.font_size / pesos.font_size : 0.47;
      const dollarSize = mainSize * dollarRatio;
      const maxWidthTarget = maxWidth;

      if (!isVertical) {
        // --- Horizontal: "2x$45" en una sola línea, "$" chico y centrado
        // verticalmente contra los números grandes (mismo criterio que el "$"
        // del modo precio). ---
        const bigPartText = `${multiplierText}x${priceIntegerText}`;
        const widthAtMainBase = measureTextWidth(bigPartText, weight, mainSize);
        const widthDollarBase = measureTextWidth(dollarText, weight, dollarSize);
        const totalWidthBase = widthAtMainBase + widthDollarBase;
        const scale = totalWidthBase > maxWidthTarget ? maxWidthTarget / totalWidthBase : 1;
        const scaledMain = mainSize * scale;
        const scaledDollar = dollarSize * scale;
        const totalWidth = totalWidthBase * scale;

        const fullTextForAscent = `${multiplierText}x${dollarText}${priceIntegerText}`;
        const baselineY = cursorY + scaledMain * ascentRatioFor(fullTextForAscent);
        const shift = ((scaledMain - scaledDollar) * 0.72) / 2;
        const tspans =
          `<tspan fill="${multiplierColor}" font-size="${scaledMain.toFixed(1)}">${escapeXml(multiplierText)}x</tspan>` +
          `<tspan fill="${priceColor}" font-size="${scaledDollar.toFixed(1)}" baseline-shift="${shift.toFixed(1)}">${escapeXml(dollarText)}</tspan>` +
          `<tspan fill="${priceColor}" font-size="${scaledMain.toFixed(1)}">${escapeXml(priceIntegerText)}</tspan>`;
        bodyParts.push(`<text x="${centerX}" y="${baselineY}" text-anchor="middle" font-family="${FONT_FAMILY_CSS}" font-weight="${weight}">${tspans}</text>`);

        const lineHeight = paragraphHeight(1, scaledMain, fullTextForAscent);
        trackX(centerX - (totalWidth) / 2, centerX + (totalWidth) / 2);
        trackY(cursorY, cursorY + lineHeight);
        cursorY += lineHeight;
      } else {
        // --- Vertical: "Nx" arriba (negro), "$PPP" abajo ("$" chico y
        // centrado, dígitos grandes en rojo) — espacio entre líneas más
        // ajustado que el estándar (no es un párrafo, es un bloque compacto). ---
        const topText = `${multiplierText}x`;
        const topWidthBase = measureTextWidth(topText, weight, mainSize);
        const bottomWidthBase = measureTextWidth(dollarText, weight, dollarSize) + measureTextWidth(priceIntegerText, weight, mainSize);
        const scaleTop = topWidthBase > maxWidthTarget ? maxWidthTarget / topWidthBase : 1;
        const scaleBottom = bottomWidthBase > maxWidthTarget ? maxWidthTarget / bottomWidthBase : 1;
        const scale = Math.min(scaleTop, scaleBottom);

        const scaledMain = mainSize * scale;
        const scaledDollar = dollarSize * scale;
        const topWidth = topWidthBase * scale;
        const bottomWidth = bottomWidthBase * scale;

        const TIGHT_LINE_RATIO = 0.87; // más ajustado que el 1.15 estándar de párrafos (se redujo un poco más de 0.95 para ganar margen de seguridad — ver el caso "combinado vertical con Callout+Frase+Brand" que se pasaba por ~80px)
        const lineHeightPer = scaledMain * TIGHT_LINE_RATIO;

        const topBaselineY = cursorY + scaledMain * ascentRatioFor(topText);
        const bottomBaselineY = topBaselineY + lineHeightPer;

        bodyParts.push(`<text x="${centerX}" y="${topBaselineY}" text-anchor="middle" font-family="${FONT_FAMILY_CSS}" font-weight="${weight}" font-size="${scaledMain.toFixed(1)}" fill="${multiplierColor}">${escapeXml(topText)}</text>`);

        const shift = ((scaledMain - scaledDollar) * 0.72) / 2;
        const bottomTspans =
          `<tspan fill="${priceColor}" font-size="${scaledDollar.toFixed(1)}" baseline-shift="${shift.toFixed(1)}">${escapeXml(dollarText)}</tspan>` +
          `<tspan fill="${priceColor}" font-size="${scaledMain.toFixed(1)}">${escapeXml(priceIntegerText)}</tspan>`;
        bodyParts.push(`<text x="${centerX}" y="${bottomBaselineY}" text-anchor="middle" font-family="${FONT_FAMILY_CSS}" font-weight="${weight}">${bottomTspans}</text>`);

        // El colchón final es chico (0.05) porque "Nx"/"$PPP" son dígitos y
        // letras sin descendente real — un colchón grande ahí es espacio
        // desperdiciado que además empuja lo que sigue (marca) hacia abajo.
        const totalHeight = scaledMain * ascentRatioFor(topText) + lineHeightPer + scaledMain * 0.05;
        trackX(centerX - (Math.max(topWidth, bottomWidth)) / 2, centerX + (Math.max(topWidth, bottomWidth)) / 2);
        trackY(cursorY, cursorY + totalHeight);
        cursorY += totalHeight;
      }
    } else if (pesos.price && pesos.price.mode === "quantity") {
      // --- Modo cantidad: "2x3", "6x2", etc. — sin "$", sin "%", sin "desc",
      // sin "c/u". Solo los dos números al mismo tamaño, con "x" minúscula,
      // todo en negro, ajustado al ancho máximo posible de la plantilla. ---
      const quantityText = `${pesos.price.left}x${pesos.price.right}`;
      // Color explícito de la celda MULTIPLICADOR en Excel, si lo hay, pisa
      // el negro fijo de este modo.
      const quantityColor = pesos.price.color || "rgb(0,0,0)";
      const fittedSize = fitFontSizeToWidth(quantityText, weight, pesos.font_size, maxWidth);
      const lineWidth = measureTextWidth(quantityText, weight, fittedSize);
      const baselineY = cursorY + fittedSize * ascentRatioFor(quantityText);
      bodyParts.push(`<text x="${centerX}" y="${baselineY}" text-anchor="middle" font-family="${FONT_FAMILY_CSS}" font-weight="${weight}" font-size="${fittedSize.toFixed(1)}" fill="${quantityColor}">${escapeXml(quantityText)}</text>`);
      const lineHeight = paragraphHeight(1, fittedSize, quantityText);
      trackX(centerX - (lineWidth) / 2, centerX + (lineWidth) / 2);
      trackY(cursorY, cursorY + lineHeight);
      cursorY += lineHeight;
    } else if (pesos.price) {
      // Color explícito de la celda PESOS O PRECIO en Excel, si lo hay, pisa
      // el color fijo de la plantilla — tanto para el "$"/prefijo como para
      // el entero y el grupo apilado de centavos/c-u, para que todo el
      // precio se vea como un mismo color coherente (igual que ya venía
      // pasando entre pesos.color y pesos.prefix.color en la plantilla).
      const priceColor = pesos.price.color || pesos.color;
      const prefixColor = pesos.price.color || (pesos.prefix ? pesos.prefix.color : pesos.color);

      // --- Línea principal: "$" + entero, centrada como antes ---
      const mainLine = [];
      if (pesos.prefix) {
        mainLine.push({ text: pesos.prefix.text, _fontSize: pesos.prefix.font_size, _weight: prefixWeight, color: prefixColor });
      }
      mainLine.push({ text: pesos.price.integer, _fontSize: pesos.font_size, _weight: weight, color: priceColor });
      const mainMaxFontSize = Math.max(...mainLine.map((w) => w._fontSize));

      // --- Grupo apilado a la derecha: centavos arriba, "c/u" abajo, uno bajo el
      // otro. Entre los dos (apilados) ocupan la misma altura que el número
      // entero — por eso cada uno usa la mitad del tamaño de fuente del entero
      // (mitad de tamaño ≈ mitad de altura de mayúscula). "c/u" además es 30%
      // más chico que ese tamaño de centavos. Si solo viene uno de los dos, se
      // centra solo en esa mitad de altura arriba, más simple.
      const hasStack = !!(pesos.price.cents || pesos.price.has_cu);
      const centsFontSize = pesos.font_size * 0.5;
      const cuFontSize = centsFontSize * 0.7; // 30% más chico que los centavos
      const smallWeight = prefixWeight;
      const smallColor = prefixColor;
      const centsText = pesos.price.cents ? "." + pesos.price.cents : null;
      const cuText = pesos.price.has_cu ? "c/u" : null;

      // --- Escala conjunta si todo (línea principal + grupo apilado) no cabe en
      // el 90% del ancho de la plantilla — mismo factor para todo, así nada
      // queda desproporcionado entre sí.
      const gapBase = pesos.font_size * 0.06;
      const mainWidthBase = mainLine.reduce((w, word) => w + measureTextWidth(word.text, word._weight, word._fontSize), 0);
      // Ancho del grupo apilado = el más ancho de los dos (centavos o c/u), ya
      // que van alineados a la izquierda entre sí (no centrados uno con otro).
      const stackWidthBase = hasStack ? Math.max(
        centsText ? measureTextWidth(centsText, smallWeight, centsFontSize) : 0,
        cuText ? measureTextWidth(cuText, smallWeight, cuFontSize) : 0
      ) : 0;
      const totalWidthBase = mainWidthBase + (hasStack ? gapBase + stackWidthBase : 0);
      const maxPesosWidth = maxWidth;
      const scale = totalWidthBase > maxPesosWidth ? maxPesosWidth / totalWidthBase : 1;

      const mainWidth = mainWidthBase * scale;
      const stackWidth = stackWidthBase * scale;
      const gap = gapBase * scale;
      const scaledCentsFontSize = centsFontSize * scale;
      const scaledCuFontSize = cuFontSize * scale;
      const scaledMainMaxFontSize = mainMaxFontSize * scale;
      const totalWidth = mainWidth + (hasStack ? gap + stackWidth : 0);

      // Centrar el CONJUNTO completo (línea principal + grupo apilado) sobre el
      // ancho de la plantilla, no solo la línea principal por separado.
      const mainCenterX = centerX - totalWidth / 2 + mainWidth / 2;
      const stackCenterX = mainCenterX + mainWidth / 2 + gap + stackWidth / 2;
      const stackLeftX = stackCenterX - stackWidth / 2; // borde izquierdo común

      const plainMainText = mainLine.map((w) => w.text).join("");
      const baselineY = cursorY + scaledMainMaxFontSize * ascentRatioFor(plainMainText);

      // línea principal: tspans con baseline-shift para centrar el "$" contra
      // la altura de mayúscula del entero (igual que antes)
      const mainTspans = mainLine.map((w) => {
        const fSize = w._fontSize * scale;
        let attrs = ` font-weight="${w._weight}" font-size="${fSize.toFixed(1)}" fill="${w.color}"`;
        if (fSize < scaledMainMaxFontSize) {
          const shift = ((scaledMainMaxFontSize - fSize) * 0.72) / 2;
          attrs += ` baseline-shift="${shift.toFixed(1)}"`;
        }
        return `<tspan${attrs}>${escapeXml(w.text)}</tspan>`;
      }).join("");
      bodyParts.push(`<text x="${mainCenterX}" y="${baselineY}" text-anchor="middle" font-family="${FONT_FAMILY_CSS}">${mainTspans}</text>`);

      // Si vienen AMBOS (centavos y c/u), van apilados: centavos arriba (tope
      // alineado con el tope del entero), "c/u" abajo (base alineada con la
      // línea base del entero), alineados a la izquierda entre sí. Si viene
      // SOLO UNO de los dos, no hay nada con qué "apilarse" — se centra
      // verticalmente igual que el "$" (mismo criterio, mismo look).
      const bothPresent = !!(centsText && cuText);
      if (bothPresent) {
        // Antes el fondo de los centavos se apoyaba justo donde empieza el
        // tope de "c/u" (sin aire entre los dos, se veían pegados). Ahora se
        // separan un 15% — para no agrandar el conjunto apilado (y que no
        // invada el número principal), ese aire se saca reduciendo un poco
        // AMBOS tamaños (centavos y c/u), no solo agregando espacio extra.
        const STACK_GAP_RATIO = 0.15;
        const stackGap = STACK_GAP_RATIO * scaledCuFontSize;
        const stackShrink = 1 - stackGap / (scaledCentsFontSize + scaledCuFontSize);
        const stackedCentsFontSize = scaledCentsFontSize * stackShrink;
        const stackedCuFontSize = scaledCuFontSize * stackShrink;

        const centsBaselineY = baselineY - stackedCuFontSize * 0.72 - stackGap;
        bodyParts.push(`<text x="${stackLeftX}" y="${centsBaselineY}" text-anchor="start" font-family="${FONT_FAMILY_CSS}" font-weight="${smallWeight}" font-size="${stackedCentsFontSize.toFixed(1)}" fill="${smallColor}">${escapeXml(centsText)}</text>`);
        const cuBaselineY = baselineY;
        bodyParts.push(`<text x="${stackLeftX}" y="${cuBaselineY}" text-anchor="start" font-family="${FONT_FAMILY_CSS}" font-weight="${smallWeight}" font-size="${stackedCuFontSize.toFixed(1)}" fill="${smallColor}">${escapeXml(cuText)}</text>`);
      } else if (centsText) {
        const shift = ((scaledMainMaxFontSize - scaledCentsFontSize) * 0.72) / 2;
        bodyParts.push(`<text x="${stackCenterX}" y="${baselineY}" text-anchor="middle" font-family="${FONT_FAMILY_CSS}" font-weight="${smallWeight}" font-size="${scaledCentsFontSize.toFixed(1)}" fill="${smallColor}" baseline-shift="${shift.toFixed(1)}">${escapeXml(centsText)}</text>`);
      } else if (cuText) {
        const shift = ((scaledMainMaxFontSize - scaledCuFontSize) * 0.72) / 2;
        bodyParts.push(`<text x="${stackCenterX}" y="${baselineY}" text-anchor="middle" font-family="${FONT_FAMILY_CSS}" font-weight="${smallWeight}" font-size="${scaledCuFontSize.toFixed(1)}" fill="${smallColor}" baseline-shift="${shift.toFixed(1)}">${escapeXml(cuText)}</text>`);
      }

      const lineHeight = paragraphHeight(1, scaledMainMaxFontSize, plainMainText);
      trackX(mainCenterX - mainWidth / 2, hasStack ? stackCenterX + stackWidth / 2 : mainCenterX + mainWidth / 2);
      trackY(cursorY, cursorY + lineHeight);
      cursorY += lineHeight;
    } else {
      // Respaldo: plantilla vieja / campo sin el parseo de precio — texto plano.
      const runs = pesos.runs && pesos.runs.length ? pesos.runs : [{ text: pesos.value }];
      const line = runsToWords(runs).map((w) => ({ ...w, _fontSize: pesos.font_size, _weight: weight, color: pesos.color }));
      const baseWidth = line.reduce((w, word, i) => {
        let x = w + measureTextWidth(word.text, word._weight, word._fontSize);
        if (i > 0 && line[i - 1]._spaceAfter) x += measureTextWidth(" ", weight, pesos.font_size);
        return x;
      }, 0);
      const maxPesosWidth = maxWidth;
      const scale = baseWidth > maxPesosWidth ? maxPesosWidth / baseWidth : 1;
      const scaledSize = pesos.font_size * scale;
      const tspans = line.map((w) => {
        const text = escapeXml(w.text) + (w._spaceAfter ? " " : "");
        let attrs = ` font-weight="${w._weight}" font-size="${scaledSize.toFixed(1)}" fill="${w.color}"`;
        if (w.strike) attrs += ` text-decoration="line-through"`;
        return `<tspan${attrs}>${text}</tspan>`;
      }).join("");
      const plainText = line.map((w) => w.text).join(" ");
      const baselineY = cursorY + scaledSize * ascentRatioFor(plainText);
      bodyParts.push(`<text x="${centerX}" y="${baselineY}" text-anchor="middle" font-family="${FONT_FAMILY_CSS}">${tspans}</text>`);
      const totalWidth = baseWidth * scale;
      const lineHeight = paragraphHeight(1, scaledSize, plainText);
      trackX(centerX - (totalWidth) / 2, centerX + (totalWidth) / 2);
      trackY(cursorY, cursorY + lineHeight);
      cursorY += lineHeight;
    }

  return cursorY;
}

/** `template.orientation` ("vertical" | "horizontal") decide qué motor de
 *  armado usar — el resto de la lógica de "aprovechar el espacio" de acá
 *  abajo es la MISMA para las dos orientaciones (no le importa cuál eje
 *  crece más, solo hace contain-fit contra cw/ch, y eso ya funciona igual
 *  para un lienzo alto-y-angosto que para uno ancho-y-bajo). Por defecto
 *  "vertical" — así las plantillas viejas sin este campo (extraídas antes de
 *  que existiera la horizontal) siguen andando sin tocarlas. */
function builderFor(template) {
  return template.orientation === "horizontal" ? buildSvgHorizontal : buildSvg;
}

async function buildSvgWithMaxFill(template, mapping, rawExcelFields) {
  const build = builderFor(template);
  const pass1 = await build(template, mapping, rawExcelFields);
  const cw = template.canvas.width, ch = template.canvas.height;
  if (pass1.crop.width <= 0 || pass1.crop.height <= 0) return pass1;

  const MARGIN_FACTOR = 0.96; // pequeño margen de aire, no pegar el contenido a los bordes exactos
  const scaleUp = Math.min((cw * MARGIN_FACTOR) / pass1.crop.width, (ch * MARGIN_FACTOR) / pass1.crop.height);

  // Si no hay margen real para crecer (ya aprovecha bien el espacio, o el
  // ancho ya es el límite), usar la pasada 1 tal cual — evita renders extra.
  if (!isFinite(scaleUp) || scaleUp <= 1.03) return pass1;

  const PRICE_PRIORITY_BOOST = 1.3;
  const boostedMapping = scaleMapping(mapping, scaleUp, PRICE_PRIORITY_BOOST);
  const boostedPass = await build(template, boostedMapping, rawExcelFields);
  // OJO: boostedPass.crop YA viene recortado (acotado) contra cw/ch, así que
  // compararlo contra cw/ch nunca detecta un desborde real. Hay que comparar
  // las medidas SIN recortar (rawRight/rawBottom) — esas sí reflejan si el
  // contenido genuino se pasó del lienzo.
  if (boostedPass.crop.rawRight <= cw && boostedPass.crop.rawBottom <= ch) return boostedPass;

  // Respaldo seguro: crecimiento uniforme sin impulso extra al precio.
  const uniformMapping = scaleMapping(mapping, scaleUp, 1);
  const uniformPass = await build(template, uniformMapping, rawExcelFields);
  if (uniformPass.crop.rawRight <= cw && uniformPass.crop.rawBottom <= ch) return uniformPass;

  return pass1; // último respaldo, no debería llegar aquí en la práctica
}

async function buildSvg(template, mapping, rawExcelFields) {
  const cw = template.canvas.width, ch = template.canvas.height;
  const parts = [];
  const defsParts = [];

  // Fuentes incrustadas como base64 (@font-face con data: URI) — el SVG debe ser
  // 100% autocontenido, sin peticiones de red externas (ni a Google Fonts ni a
  // ningún CDN).
  if (template.fonts) {
    const fontFaces = Object.entries(template.fonts).map(([name, dataUrl]) => {
      const weight = weightForFontName(name);
      return `@font-face { font-family: 'Work Sans'; font-weight: ${weight}; src: url(${dataUrl}) format('woff2'); }`;
    }).join("\n");
    defsParts.push(`<defs><style>${fontFaces}</style></defs>`);
  }

  const fieldsByColumn = Object.fromEntries(mapping.map((f) => [f.excel_column, f]));

  // Rastreamos el contenido real dibujado (no las cajas máximas reservadas) para
  // poder recortar el PNG/SVG final justo hasta donde hay información, sin
  // transparencia/blanco sobrante a los lados o arriba/abajo.
  let contentTop = null, contentBottom = null;
  let contentLeft = cw, contentRight = 0;
  function trackX(left, right) {
    contentLeft = Math.min(contentLeft, left);
    contentRight = Math.max(contentRight, right);
  }
  function trackY(top, bottom) {
    if (contentTop === null) contentTop = top;
    contentBottom = bottom;
  }

  const bodyParts = [];

  // --- Layout de flujo secuencial ---
  // NOTA: este orden (CALLOUT → FRASE APOYO → PESOS → DESCRIPCION → BRAND) está
  // codificado para ESTA plantilla específica. Al escalar a las otras 35, esto
  // se generalizaría a partir de un orden declarado en el propio template.json
  // (ej. un campo "flow_order": [...]), en vez de nombres de columna fijos.
  // Todo se posiciona uno debajo del otro con el MISMO espacio vertical entre
  // elementos (calculado del espacio real entre CALLOUT y FRASE APOYO en la
  // plantilla original). Cada elemento avanza el cursor según su alto REAL ya
  // renderizado (no el alto máximo reservado), así que si un texto termina más
  // corto de lo esperado, todo lo que va después sube para no dejar huecos.
  //
  // Si un campo viene VACÍO en el Excel (celda en blanco), se salta por
  // completo: no reserva espacio, no se le agrega el espacio estándar antes ni
  // después, y no entra en el recorte final al contenido.
  function isPresent(field) {
    return !!(field && field.matched && field.value && String(field.value).trim() !== "");
  }

  // Para columnas que no están declaradas en template.json (ej. "DESCRIPCION 2"
  // o el campo sintético de la variante nueva) — se buscan directo en los datos
  // crudos del Excel, no en el mapping basado en plantilla.
  function rawFieldByColumn(columnName) {
    const norm = normalize(columnName);
    return (rawExcelFields || []).find((f) => normalize(f.column) === norm) || null;
  }

  const doubleDescField = rawFieldByColumn("PROMO_DOBLE_DESCRIPCION");
  const doubleDescData = doubleDescField ? doubleDescField.doble_desc : null;
  const descripcion2Field = rawFieldByColumn("DESCRIPCION 2");
  const segundoPorField = rawFieldByColumn("PROMO_SEGUNDO_POR");
  const segundoPorData = segundoPorField ? segundoPorField.segundo_por : null;
  const soloFraseDescField = rawFieldByColumn("PROMO_SOLO_FRASE_DESC");
  const soloFraseDescData = soloFraseDescField ? soloFraseDescField.solo_frase_desc : null;
  const soloDescripcionField = rawFieldByColumn("PROMO_SOLO_DESCRIPCION");
  const soloDescripcionData = soloDescripcionField ? soloDescripcionField.solo_descripcion : null;

  const callout = fieldsByColumn["CALLOUT"];
  const fraseApoyo = fieldsByColumn["FRASE APOYO"];

  // FRASE APOYO es el texto de apertura por default en TODAS las variantes
  // (normal, doble descripción, "segundo por") — va justo antes de "COMPRA" en
  // las variantes nuevas, ya que es fundamental en la lógica de la mecánica.
  // measureFraseApoyoHeight() se usa para el presupuesto de alto (sin dibujar
  // nada); renderFraseApoyoBlock() hace el dibujo real, avanzando cursorY.
  function measureFraseApoyoHeight() {
    if (!isPresent(fraseApoyo)) return 0;
    const weight = weightForFontName(fraseApoyo.font_family);
    const runs = fraseApoyo.runs && fraseApoyo.runs.length ? fraseApoyo.runs : [{ text: fraseApoyo.value }];
    const plainText = runs.map((r) => r.text).join("");
    const fittedSize = fitFontSizeToWidth(plainText, weight, fraseApoyo.font_size, cw * 0.9);
    return paragraphHeight(1, fittedSize, plainText);
  }

  function renderFraseApoyoBlock() {
    if (!isPresent(fraseApoyo)) return;
    advance();
    const weight = weightForFontName(fraseApoyo.font_family);
    const runs = fraseApoyo.runs && fraseApoyo.runs.length ? fraseApoyo.runs : [{ text: fraseApoyo.value }];
    const plainText = runs.map((r) => r.text).join("");
    const maxWidth = cw * 0.9;
    const fittedSize = fitFontSizeToWidth(plainText, weight, fraseApoyo.font_size, maxWidth);
    const lineWidth = measureTextWidth(plainText, weight, fittedSize);
    const baselineY = cursorY + fittedSize * ascentRatioFor(plainText);
    bodyParts.push(lineToSvgText(runsToWords(runs), weight, fittedSize, fraseApoyo.color, cw / 2, baselineY));
    const lineHeight = paragraphHeight(1, fittedSize, plainText);
    trackX((cw - lineWidth) / 2, (cw + lineWidth) / 2);
    trackY(cursorY, cursorY + lineHeight);
    cursorY += lineHeight;
  }

  const GAP_MULTIPLIER = 1.15; // espacio estándar = espacio original de la plantilla + 15%
  const gap = (callout && fraseApoyo
    ? fraseApoyo.box.y - (callout.box.y + callout.box.height)
    : 187) * GAP_MULTIPLIER;

  let cursorY = 0;
  let hasPrevious = false;
  function advance(factor = 1) {
    if (hasPrevious) cursorY += gap * factor;
    hasPrevious = true;
  }

  if (doubleDescData) {
    await renderDoubleDescriptionFlow();
  } else if (segundoPorData) {
    await renderSegundoPorFlow();
  } else if (soloDescripcionData) {
    await renderOnlyDescriptionFlow();
  } else {
    await renderNormalFlow(soloFraseDescData ? 8 : 4);
  }

  async function renderOnlyDescriptionFlow() {
    // --- Variante "solo descripción": SIN CALLOUT ni BRAND en ningún caso
    // (aunque el Excel los traiga), sin FRASE APOYO, sin COMPRA — únicamente
    // la descripción, centrada, hasta 20 líneas, usando el alto total de la
    // plantilla (con un margen de aire arriba/abajo). ---
    const descripcion = fieldsByColumn["DESCRIPCION"];
    if (!isPresent(descripcion)) return;
    const weight = weightForFontName(descripcion.font_family);
    const runs = descripcion.runs && descripcion.runs.length ? descripcion.runs : [{ text: descripcion.value }];
    const maxHeight = ch - 2 * gap;
    const { fontSize: fittedSize, lines, actualHeight, maxLineWidth } =
      fitParagraph(runs, weight, descripcion.font_size, descripcion.box.width, maxHeight, 20);
    const plainText = lines.map((l) => l.map((w) => w.text).join(" ")).join(" ");
    const firstLineAscent = ascentRatioFor(plainText);
    cursorY = gap;
    lines.forEach((line, i) => {
      const baselineY = cursorY + fittedSize * firstLineAscent + i * fittedSize * LINE_HEIGHT_RATIO;
      bodyParts.push(lineToSvgText(line, weight, fittedSize, descripcion.color, cw / 2, baselineY));
    });
    trackX((cw - maxLineWidth) / 2, (cw + maxLineWidth) / 2);
    trackY(cursorY, cursorY + actualHeight);
  } // fin renderOnlyDescriptionFlow

  async function renderNormalFlow(descripcionMaxLines = 4) {

  if (isPresent(callout)) {
    advance();
    const dataUrl = await fetchDynamicAssetDataUrl(callout.value);
    const natural = await loadImageNaturalSize(dataUrl);
    const disp = containFit(natural.w, natural.h, callout.box.width, callout.box.height);
    const dispLeft = callout.box.x + (callout.box.width - disp.w) / 2;
    // preserveAspectRatio="xMidYMid meet" = ajuste tipo "contain": cabe completo
    // dentro de la caja, centrado, sin deformarse — nativo de SVG, sin cálculos.
    bodyParts.push(`<image href="${dataUrl}" xlink:href="${dataUrl}" x="${callout.box.x}" y="${cursorY}" width="${callout.box.width}" height="${callout.box.height}" preserveAspectRatio="xMidYMid meet"/>`);
    trackX(dispLeft, dispLeft + disp.w);
    trackY(cursorY, cursorY + callout.box.height);
    cursorY += callout.box.height;
  }

  if (isPresent(fraseApoyo)) {
    advance();
    const weight = weightForFontName(fraseApoyo.font_family);
    const runs = fraseApoyo.runs && fraseApoyo.runs.length ? fraseApoyo.runs : [{ text: fraseApoyo.value }];
    const plainText = runs.map((r) => r.text).join("");
    const maxWidth = cw * 0.9;
    const fittedSize = fitFontSizeToWidth(plainText, weight, fraseApoyo.font_size, maxWidth);
    const lineWidth = measureTextWidth(plainText, weight, fittedSize);
    const baselineY = cursorY + fittedSize * ascentRatioFor(plainText);
    bodyParts.push(lineToSvgText(runsToWords(runs), weight, fittedSize, fraseApoyo.color, cw / 2, baselineY));
    const lineHeight = paragraphHeight(1, fittedSize, plainText);
    trackX((cw - lineWidth) / 2, (cw + lineWidth) / 2);
    trackY(cursorY, cursorY + lineHeight);
    cursorY += lineHeight;
  }

  const pesos = fieldsByColumn["PESOS"];
  if (isPresent(pesos)) {
    // El bloque de PESOS (precio o porcentaje) usa una fuente enorme, y su
    // fórmula de altura reserva bastante espacio de ascenso — eso hacía ver el
    // espacio ANTES de este bloque más grande de lo esperado, en ambos modos.
    // Se reduce solo para esta transición específica, sin tocar el espacio
    // estándar del resto del flujo.
    advance(0.75);
    const weight = weightForFontName(pesos.font_family);
    const prefixWeight = pesos.prefix ? weightForFontName(pesos.prefix.font_family) : weight;
    cursorY = renderPesosBlock(pesos, weight, prefixWeight, cw / 2, cw * 0.9, cursorY, bodyParts, trackX, trackY);
  }

  const descripcion = fieldsByColumn["DESCRIPCION"];
  if (isPresent(descripcion)) {
    advance();
    const weight = weightForFontName(descripcion.font_family);
    const runs = descripcion.runs && descripcion.runs.length ? descripcion.runs : [{ text: descripcion.value }];
    // Hasta 4 líneas al tamaño base (usa el alto original de la caja como tope,
    // escalado si el máximo de líneas es mayor, ej. la variante "solo frase +
    // descripción" que permite hasta 8); si excede, se reduce el tamaño hasta
    // que quepa todo. Si el texto es corto (menos líneas), usa menos alto y lo
    // que sigue (BRAND) sube para no dejar hueco.
    const descripcionMaxHeight = descripcion.box.height * (descripcionMaxLines / 4);
    const { fontSize: fittedSize, lines, actualHeight, maxLineWidth } =
      fitParagraph(runs, weight, descripcion.font_size, descripcion.box.width, descripcionMaxHeight, descripcionMaxLines);

    const descripcionPlainText = lines.map((l) => l.map((w) => w.text).join(" ")).join(" ");
    const firstLineAscent = ascentRatioFor(descripcionPlainText);
    lines.forEach((line, i) => {
      const baselineY = cursorY + fittedSize * firstLineAscent + i * fittedSize * LINE_HEIGHT_RATIO;
      bodyParts.push(lineToSvgText(line, weight, fittedSize, descripcion.color, cw / 2, baselineY));
    });

    trackX((cw - maxLineWidth) / 2, (cw + maxLineWidth) / 2);
    trackY(cursorY, cursorY + actualHeight);
    cursorY += actualHeight;
  }

  const brand = fieldsByColumn["BRAND"];
  if (isPresent(brand)) {
    advance();
    const dataUrl = await fetchDynamicAssetDataUrl(brand.value);
    const natural = await loadImageNaturalSize(dataUrl);
    const disp = containFit(natural.w, natural.h, brand.box.width, brand.box.height);
    const dispLeft = brand.box.x + (brand.box.width - disp.w) / 2;
    bodyParts.push(`<image href="${dataUrl}" xlink:href="${dataUrl}" x="${brand.box.x}" y="${cursorY}" width="${brand.box.width}" height="${brand.box.height}" preserveAspectRatio="xMidYMid meet"/>`);
    trackX(dispLeft, dispLeft + disp.w);
    trackY(cursorY, cursorY + brand.box.height);
  }
  } // fin renderNormalFlow

  async function renderDoubleDescriptionFlow() {
    // --- Variante "promo doble descripción": COMPRA (fijo) + descripción 1 +
    // [caso 1: "+$NN" + banner LLÉVATE | caso 2: banner Y LLÉVATE + "GRATIS"]
    // + descripción 2. El CALLOUT se muestra solo en el caso 2 (dinámico, del
    // Excel); en el caso 1 se suprime siempre, aunque el Excel traiga uno.
    //
    // REGLA DURA: todo el conjunto debe caber dentro de los 8000px de alto de
    // la plantilla, sin excepción. Para garantizarlo, primero se MIDE (sin
    // dibujar nada) cuánto van a ocupar CALLOUT/COMPRA/la sección del medio —
    // eso es "fijo" — y solo con lo que sobra se calcula cuánto alto máximo
    // puede usar CADA descripción (si el texto es corto, no se nota; si es
    // largo, ambas reducen su tamaño lo que haga falta para no pasarse). ---
    const callout = fieldsByColumn["CALLOUT"];
    const descripcion1 = fieldsByColumn["DESCRIPCION"]; // misma columna de siempre = descripción 1
    const pesosMeta = fieldsByColumn["PESOS"]; // para reusar fuente/color del "$" y del precio
    const showCallout = doubleDescData.case === 2 && isPresent(callout);

    // --- 1. Medir CALLOUT (si aplica) ---
    let calloutDataUrl = null, calloutDisp = null;
    if (showCallout) {
      calloutDataUrl = await fetchDynamicAssetDataUrl(callout.value);
      const natural = await loadImageNaturalSize(calloutDataUrl);
      calloutDisp = containFit(natural.w, natural.h, callout.box.width, callout.box.height);
    }

    // --- 2. Medir "COMPRA" ---
    const fraseApoyoHeight = measureFraseApoyoHeight();
    const compraFont = fraseApoyo ? fraseApoyo.font_family : "WorkSans-Black";
    const compraSize = fraseApoyo ? fraseApoyo.font_size : 727;
    const compraWeight = weightForFontName(compraFont);
    const compraText = "COMPRA";
    const compraFittedSize = fitFontSizeToWidth(compraText, compraWeight, compraSize, cw * 0.9);
    const compraWidth = measureTextWidth(compraText, compraWeight, compraFittedSize);
    const compraHeight = paragraphHeight(1, compraFittedSize, compraText);

    // --- 3. Medir la sección del medio (según el caso) ---
    let middleHeight = 0;
    let priceInfo = null, bannerInfo = null, gratisInfo = null;

    if (doubleDescData.case === 1) {
      const dollarText = pesosMeta && pesosMeta.prefix ? pesosMeta.prefix.text : "$";
      // Color explícito de la celda PESOS O PRECIO en Excel, si lo hay, pisa
      // el color fijo de la plantilla (igual criterio que en renderPesosBlock).
      const priceColor = doubleDescData.price_color || (pesosMeta ? pesosMeta.color : "rgb(220,39,38)");
      const mainSize = (pesosMeta ? pesosMeta.font_size * 0.45 : 1200) * 1.7; // 70% más grande que el tamaño base original
      const smallSize = pesosMeta && pesosMeta.prefix ? mainSize * (pesosMeta.prefix.font_size / pesosMeta.font_size) : mainSize * 0.47;
      const weight = pesosMeta ? weightForFontName(pesosMeta.font_family) : 900;
      const priceText = doubleDescData.price_integer;

      const widthBase = measureTextWidth("+", weight, smallSize) + measureTextWidth(dollarText, weight, smallSize) + measureTextWidth(priceText, weight, mainSize);
      const maxW = cw * 0.9;
      const scale = widthBase > maxW ? maxW / widthBase : 1;
      const scaledMain = mainSize * scale, scaledSmall = smallSize * scale;
      const priceLineWidth = widthBase * scale;
      const priceLineHeight = paragraphHeight(1, scaledMain, priceText);

      const bannerDataUrl = await fetchAssetDataUrl("CAFIJO_LLEVATE.svg");
      const natural = await loadImageNaturalSize(bannerDataUrl);
      const bannerBox = callout ? callout.box : { width: cw * 0.6, height: 1300 };
      const disp = containFit(natural.w, natural.h, bannerBox.width, bannerBox.height);

      priceInfo = { dollarText, priceColor, weight, priceText, scaledMain, scaledSmall, priceLineWidth, priceLineHeight };
      bannerInfo = { dataUrl: bannerDataUrl, disp };
      middleHeight = priceLineHeight + gap + disp.h;
    } else {
      const bannerBox = callout ? callout.box : { width: cw * 0.5, height: 1300 };
      const bannerDataUrl = await fetchAssetDataUrl("CAFIJO_Y_LLEVATE.svg");
      const natural = await loadImageNaturalSize(bannerDataUrl);
      const targetHeight = bannerBox.height * 0.85;
      const disp = containFit(natural.w, natural.h, bannerBox.width * 0.65, targetHeight);

      const gratisWeight = fraseApoyo ? weightForFontName(fraseApoyo.font_family) : 900;
      const gratisFontSize = disp.h * 0.9;
      const gratisText = "GRATIS";
      const gratisWidth = measureTextWidth(gratisText, Math.max(gratisWeight, 700), gratisFontSize);
      const gapBetween = disp.h * 0.15;
      const totalWidth = disp.w + gapBetween + gratisWidth;

      bannerInfo = { dataUrl: bannerDataUrl, disp, totalWidth, gapBetween };
      gratisInfo = { gratisWeight, gratisFontSize, gratisText, gratisWidth };
      middleHeight = disp.h;
    }

    // --- 4. Presupuesto para las 2 descripciones: lo que sobra después de todo
    // lo fijo (con sus gaps) y un margen de seguridad al final. Se reparte
    // 50/50 — así, sin importar cuánto texto traiga cada una, la SUMA nunca
    // puede pasarse de los 8000px de la plantilla. ---
    const fixedHeight = (showCallout ? callout.box.height + gap : 0) + (fraseApoyoHeight ? fraseApoyoHeight + gap : 0) + gap + compraHeight + gap + middleHeight;
    const safetyMargin = gap * 2.5; // colchón amplio: el ajustado (1x gap) dejaba solo ~30px de margen real, muy poco ante cualquier variación de métricas de fuente
    const availableForDescriptions = Math.max(
      compraFittedSize * 2, // piso de seguridad, no debería alcanzarse en la práctica
      ch - fixedHeight - 2 * gap - safetyMargin
    );
    const maxHeightPerDescription = availableForDescriptions / 2;

    // --- 5. Renderizar todo en orden, ya con el presupuesto calculado ---
    if (showCallout) {
      advance();
      bodyParts.push(`<image href="${calloutDataUrl}" xlink:href="${calloutDataUrl}" x="${callout.box.x}" y="${cursorY}" width="${callout.box.width}" height="${callout.box.height}" preserveAspectRatio="xMidYMid meet"/>`);
      const dispLeft = callout.box.x + (callout.box.width - calloutDisp.w) / 2;
      trackX(dispLeft, dispLeft + calloutDisp.w);
      trackY(cursorY, cursorY + callout.box.height);
      cursorY += callout.box.height;
    }

    renderFraseApoyoBlock();

    {
      advance();
      const baselineY = cursorY + compraFittedSize * ascentRatioFor(compraText);
      bodyParts.push(`<text x="${cw / 2}" y="${baselineY}" text-anchor="middle" font-family="${FONT_FAMILY_CSS}" font-weight="${compraWeight}" font-size="${compraFittedSize.toFixed(1)}" fill="rgb(0,0,0)">${escapeXml(compraText)}</text>`);
      trackX((cw - compraWidth) / 2, (cw + compraWidth) / 2);
      trackY(cursorY, cursorY + compraHeight);
      cursorY += compraHeight;
    }

    async function renderDescriptionBlock(field, maxHeight) {
      if (!isPresent(field)) return;
      advance();
      const weight = weightForFontName(field.font_family);
      const runs = field.runs && field.runs.length ? field.runs : [{ text: field.value }];
      const { fontSize: fittedSize, lines, actualHeight, maxLineWidth } =
        fitParagraph(runs, weight, field.font_size, field.box.width, maxHeight);
      const plainText = lines.map((l) => l.map((w) => w.text).join(" ")).join(" ");
      const firstLineAscent = ascentRatioFor(plainText);
      lines.forEach((line, i) => {
        const baselineY = cursorY + fittedSize * firstLineAscent + i * fittedSize * LINE_HEIGHT_RATIO;
        bodyParts.push(lineToSvgText(line, weight, fittedSize, field.color, cw / 2, baselineY));
      });
      trackX((cw - maxLineWidth) / 2, (cw + maxLineWidth) / 2);
      trackY(cursorY, cursorY + actualHeight);
      cursorY += actualHeight;
    }

    await renderDescriptionBlock(descripcion1, maxHeightPerDescription);

    if (doubleDescData.case === 1) {
      advance();
      const p = priceInfo;
      const shift = ((p.scaledMain - p.scaledSmall) * 0.72) / 2;
      const baselineY = cursorY + p.scaledMain * ascentRatioFor(p.priceText);
      const tspans =
        `<tspan fill="rgb(0,0,0)" font-size="${p.scaledSmall.toFixed(1)}" baseline-shift="${shift.toFixed(1)}">+</tspan>` +
        `<tspan fill="${p.priceColor}" font-size="${p.scaledSmall.toFixed(1)}" baseline-shift="${shift.toFixed(1)}">${escapeXml(p.dollarText)}</tspan>` +
        `<tspan fill="${p.priceColor}" font-size="${p.scaledMain.toFixed(1)}">${escapeXml(p.priceText)}</tspan>`;
      bodyParts.push(`<text x="${cw / 2}" y="${baselineY}" text-anchor="middle" font-family="${FONT_FAMILY_CSS}" font-weight="${p.weight}">${tspans}</text>`);
      trackX((cw - p.priceLineWidth) / 2, (cw + p.priceLineWidth) / 2);
      trackY(cursorY, cursorY + p.priceLineHeight);
      cursorY += p.priceLineHeight;

      advance();
      const b = bannerInfo;
      const dispLeft = cw / 2 - b.disp.w / 2;
      bodyParts.push(`<image href="${b.dataUrl}" xlink:href="${b.dataUrl}" x="${dispLeft}" y="${cursorY}" width="${b.disp.w}" height="${b.disp.h}" preserveAspectRatio="xMidYMid meet"/>`);
      trackX(dispLeft, dispLeft + b.disp.w);
      trackY(cursorY, cursorY + b.disp.h);
      cursorY += b.disp.h;
    } else {
      advance();
      const b = bannerInfo, g = gratisInfo;
      const bannerLeft = cw / 2 - b.totalWidth / 2;
      const gratisLeft = bannerLeft + b.disp.w + b.gapBetween;
      bodyParts.push(`<image href="${b.dataUrl}" xlink:href="${b.dataUrl}" x="${bannerLeft}" y="${cursorY}" width="${b.disp.w}" height="${b.disp.h}" preserveAspectRatio="xMidYMid meet"/>`);
      const gratisBaselineY = cursorY + b.disp.h * ascentRatioFor(g.gratisText);
      bodyParts.push(`<text x="${gratisLeft}" y="${gratisBaselineY}" text-anchor="start" font-family="${FONT_FAMILY_CSS}" font-weight="${Math.max(g.gratisWeight, 700)}" font-size="${g.gratisFontSize.toFixed(1)}" fill="rgb(0,0,0)">${escapeXml(g.gratisText)}</text>`);
      trackX(bannerLeft, gratisLeft + g.gratisWidth);
      trackY(cursorY, cursorY + b.disp.h);
      cursorY += b.disp.h;
    }

    // Descripción 2 (columna nueva "DESCRIPCION 2", mismas reglas visuales que
    // la descripción normal, con el presupuesto de alto ya calculado arriba).
    if (descripcion2Field && descripcion1) {
      await renderDescriptionBlock({ ...descripcion1, value: descripcion2Field.value, runs: descripcion2Field.runs }, maxHeightPerDescription);
    }

    // BRAND NO aplica en esta variante, aunque el Excel traiga un logo — el
    // espacio se reserva completo para las dos descripciones, sin sacrificar
    // más tamaño del necesario en ellas.
  } // fin renderDoubleDescriptionFlow

  async function renderSegundoPorFlow() {
    // --- Variante "y llévate el segundo por": CALLOUT (opcional, dinámico) +
    // COMPRA (fijo) + descripción (1 sola, máx. 4 líneas, mismo responsive de
    // siempre) + banner fijo "Y LLÉVATE" + texto fijo "EL SEGUNDO CON" +
    // [caso 1: "$NN" | caso 2: "NN% desc"]. Sin BRAND en ningún caso.
    //
    // Mismo principio de presupuesto que la variante de doble descripción:
    // primero se MIDE todo lo fijo (sin dibujar), y con lo que sobra se
    // calcula el alto máximo de la descripción — así nunca se pasa de los
    // 8000px de la plantilla, sin importar cuánto texto traiga. ---
    const callout = fieldsByColumn["CALLOUT"];
    const descripcion = fieldsByColumn["DESCRIPCION"];
    const pesosMeta = fieldsByColumn["PESOS"];
    const showCallout = isPresent(callout);

    // --- 1. Medir CALLOUT (si aplica) ---
    let calloutDataUrl = null, calloutDisp = null;
    if (showCallout) {
      calloutDataUrl = await fetchDynamicAssetDataUrl(callout.value);
      const natural = await loadImageNaturalSize(calloutDataUrl);
      calloutDisp = containFit(natural.w, natural.h, callout.box.width, callout.box.height);
    }

    const fraseApoyoHeight = measureFraseApoyoHeight();
    // --- 2. Medir "COMPRA" ---
    const compraFont = fraseApoyo ? fraseApoyo.font_family : "WorkSans-Black";
    const compraSize = fraseApoyo ? fraseApoyo.font_size : 727;
    const compraWeight = weightForFontName(compraFont);
    const compraText = "COMPRA";
    const compraFittedSize = fitFontSizeToWidth(compraText, compraWeight, compraSize, cw * 0.9);
    const compraWidth = measureTextWidth(compraText, compraWeight, compraFittedSize);
    const compraHeight = paragraphHeight(1, compraFittedSize, compraText);

    // --- 3. Medir banner "Y LLÉVATE" + texto fijo "EL SEGUNDO CON" (apilados) ---
    const bannerBox = callout ? callout.box : { width: cw * 0.6, height: 1300 };
    const bannerDataUrl = await fetchAssetDataUrl("CAFIJO_Y_LLEVATE.svg");
    const bannerNatural = await loadImageNaturalSize(bannerDataUrl);
    const bannerDisp = containFit(bannerNatural.w, bannerNatural.h, bannerBox.width, bannerBox.height);

    const elSegundoText = segundoPorData.case === 1 ? "EL SEGUNDO POR" : "EL SEGUNDO CON";
    const elSegundoWeight = fraseApoyo ? weightForFontName(fraseApoyo.font_family) : 900;
    const elSegundoBaseSize = fraseApoyo ? fraseApoyo.font_size : 727;
    const elSegundoFittedSize = fitFontSizeToWidth(elSegundoText, elSegundoWeight, elSegundoBaseSize, cw * 0.9);
    const elSegundoWidth = measureTextWidth(elSegundoText, elSegundoWeight, elSegundoFittedSize);
    const elSegundoHeight = paragraphHeight(1, elSegundoFittedSize, elSegundoText);
    const bannerSectionHeight = bannerDisp.h + gap * 0.5 + elSegundoHeight;

    // --- 4. Medir la línea de precio/porcentaje (según el caso) ---
    let priceLineInfo = null;
    if (segundoPorData.case === 1) {
      const dollarText = pesosMeta && pesosMeta.prefix ? pesosMeta.prefix.text : "$";
      const priceColor = segundoPorData.price_color || (pesosMeta ? pesosMeta.color : "rgb(220,39,38)");
      const mainSize = pesosMeta ? pesosMeta.font_size * 0.45 : 1200; // tamaño moderado — sin el ×1.7 (ese es específico de la variante de doble descripción, no de esta)
      const smallSize = pesosMeta && pesosMeta.prefix ? mainSize * (pesosMeta.prefix.font_size / pesosMeta.font_size) : mainSize * 0.47;
      const weight = pesosMeta ? weightForFontName(pesosMeta.font_family) : 900;
      const priceText = segundoPorData.price_integer;
      const widthBase = measureTextWidth(dollarText, weight, smallSize) + measureTextWidth(priceText, weight, mainSize);
      const maxW = cw * 0.9;
      const scale = widthBase > maxW ? maxW / widthBase : 1;
      const scaledMain = mainSize * scale, scaledSmall = smallSize * scale;
      priceLineInfo = { type: "price", dollarText, priceColor, weight, priceText, scaledMain, scaledSmall, width: widthBase * scale, height: paragraphHeight(1, scaledMain, priceText) };
    } else {
      const numberText = segundoPorData.percent_number;
      const weight = pesosMeta ? weightForFontName(pesosMeta.font_family) : 900;
      const mainSize = pesosMeta ? pesosMeta.font_size * 0.45 : 1200; // sin el ×1.7, mismo criterio que el caso 1 de arriba
      const percentFontSize = mainSize * 0.5;
      const percentWidthAtBase = measureTextWidth("%", weight, percentFontSize);
      const descWidthAtPercentSize = measureTextWidth("desc", weight, percentFontSize);
      const descFontSize = percentFontSize * (percentWidthAtBase / descWidthAtPercentSize);
      const color = segundoPorData.price_color || (pesosMeta ? pesosMeta.color : "rgb(220,39,38)");

      const gapBase = mainSize * 0.06;
      const numberWidthBase = measureTextWidth(numberText, weight, mainSize);
      const stackWidthBase = percentWidthAtBase;
      const totalWidthBase = numberWidthBase + gapBase + stackWidthBase;
      const maxW = cw * 0.9;
      const scale = totalWidthBase > maxW ? maxW / totalWidthBase : 1;

      const scaledMain = mainSize * scale, scaledPercent = percentFontSize * scale, scaledDesc = descFontSize * scale, scaledGap = gapBase * scale;
      const numberWidth = numberWidthBase * scale, stackWidth = stackWidthBase * scale;
      priceLineInfo = {
        type: "percent", numberText, weight, color, scaledMain, scaledPercent, scaledDesc, scaledGap,
        numberWidth, stackWidth, width: numberWidth + scaledGap + stackWidth,
        height: paragraphHeight(1, scaledMain, numberText),
      };
    }

    // --- 5. Presupuesto para la descripción (única) ---
    const fixedHeight = (showCallout ? callout.box.height + gap : 0) + (fraseApoyoHeight ? fraseApoyoHeight + gap : 0) + gap + compraHeight + gap + bannerSectionHeight + gap + priceLineInfo.height;
    const safetyMargin = gap * 2.5; // mismo colchón amplio que en renderDoubleDescriptionFlow
    const maxHeightDescription = Math.max(compraFittedSize * 2, ch - fixedHeight - gap - safetyMargin);

    // --- 6. Renderizar todo en orden ---
    if (showCallout) {
      advance();
      bodyParts.push(`<image href="${calloutDataUrl}" xlink:href="${calloutDataUrl}" x="${callout.box.x}" y="${cursorY}" width="${callout.box.width}" height="${callout.box.height}" preserveAspectRatio="xMidYMid meet"/>`);
      const dispLeft = callout.box.x + (callout.box.width - calloutDisp.w) / 2;
      trackX(dispLeft, dispLeft + calloutDisp.w);
      trackY(cursorY, cursorY + callout.box.height);
      cursorY += callout.box.height;
    }

    renderFraseApoyoBlock();

    {
      advance();
      const baselineY = cursorY + compraFittedSize * ascentRatioFor(compraText);
      bodyParts.push(`<text x="${cw / 2}" y="${baselineY}" text-anchor="middle" font-family="${FONT_FAMILY_CSS}" font-weight="${compraWeight}" font-size="${compraFittedSize.toFixed(1)}" fill="rgb(0,0,0)">${escapeXml(compraText)}</text>`);
      trackX((cw - compraWidth) / 2, (cw + compraWidth) / 2);
      trackY(cursorY, cursorY + compraHeight);
      cursorY += compraHeight;
    }

    if (isPresent(descripcion)) {
      advance();
      const weight = weightForFontName(descripcion.font_family);
      const runs = descripcion.runs && descripcion.runs.length ? descripcion.runs : [{ text: descripcion.value }];
      const { fontSize: fittedSize, lines, actualHeight, maxLineWidth } =
        fitParagraph(runs, weight, descripcion.font_size, descripcion.box.width, maxHeightDescription);
      const plainText = lines.map((l) => l.map((w) => w.text).join(" ")).join(" ");
      const firstLineAscent = ascentRatioFor(plainText);
      lines.forEach((line, i) => {
        const baselineY = cursorY + fittedSize * firstLineAscent + i * fittedSize * LINE_HEIGHT_RATIO;
        bodyParts.push(lineToSvgText(line, weight, fittedSize, descripcion.color, cw / 2, baselineY));
      });
      trackX((cw - maxLineWidth) / 2, (cw + maxLineWidth) / 2);
      trackY(cursorY, cursorY + actualHeight);
      cursorY += actualHeight;
    }

    // banner "Y LLÉVATE" + "EL SEGUNDO CON" apilados
    {
      advance();
      const dispLeft = cw / 2 - bannerDisp.w / 2;
      bodyParts.push(`<image href="${bannerDataUrl}" xlink:href="${bannerDataUrl}" x="${dispLeft}" y="${cursorY}" width="${bannerDisp.w}" height="${bannerDisp.h}" preserveAspectRatio="xMidYMid meet"/>`);
      trackX(dispLeft, dispLeft + bannerDisp.w);
      trackY(cursorY, cursorY + bannerDisp.h);
      cursorY += bannerDisp.h + gap * 0.5;

      const baselineY = cursorY + elSegundoFittedSize * ascentRatioFor(elSegundoText);
      bodyParts.push(`<text x="${cw / 2}" y="${baselineY}" text-anchor="middle" font-family="${FONT_FAMILY_CSS}" font-weight="${elSegundoWeight}" font-size="${elSegundoFittedSize.toFixed(1)}" fill="rgb(0,0,0)">${escapeXml(elSegundoText)}</text>`);
      trackX((cw - elSegundoWidth) / 2, (cw + elSegundoWidth) / 2);
      trackY(cursorY, cursorY + elSegundoHeight);
      cursorY += elSegundoHeight;
    }

    // línea de precio o porcentaje
    {
      advance();
      const p = priceLineInfo;
      if (p.type === "price") {
        const shift = ((p.scaledMain - p.scaledSmall) * 0.72) / 2;
        const baselineY = cursorY + p.scaledMain * ascentRatioFor(p.priceText);
        const tspans =
          `<tspan fill="${p.priceColor}" font-size="${p.scaledSmall.toFixed(1)}" baseline-shift="${shift.toFixed(1)}">${escapeXml(p.dollarText)}</tspan>` +
          `<tspan fill="${p.priceColor}" font-size="${p.scaledMain.toFixed(1)}">${escapeXml(p.priceText)}</tspan>`;
        bodyParts.push(`<text x="${cw / 2}" y="${baselineY}" text-anchor="middle" font-family="${FONT_FAMILY_CSS}" font-weight="${p.weight}">${tspans}</text>`);
        trackX((cw - p.width) / 2, (cw + p.width) / 2);
      } else {
        const baselineY = cursorY + p.scaledMain * ascentRatioFor(p.numberText);
        const leftEdge = cw / 2 - p.width / 2;
        const numberEndX = leftEdge + p.numberWidth;
        const stackLeftX = numberEndX + p.scaledGap;
        bodyParts.push(`<text x="${numberEndX}" y="${baselineY}" text-anchor="end" font-family="${FONT_FAMILY_CSS}" font-weight="${p.weight}" font-size="${p.scaledMain.toFixed(1)}" fill="${p.color}">${escapeXml(p.numberText)}</text>`);
        const descCapHeight = p.scaledDesc * 0.72;
        const smallGap = p.scaledMain * 0.03;
        const percentBaselineY = baselineY - descCapHeight - smallGap;
        bodyParts.push(`<text x="${stackLeftX}" y="${percentBaselineY}" text-anchor="start" font-family="${FONT_FAMILY_CSS}" font-weight="${p.weight}" font-size="${p.scaledPercent.toFixed(1)}" fill="${p.color}">%</text>`);
        bodyParts.push(`<text x="${stackLeftX}" y="${baselineY}" text-anchor="start" font-family="${FONT_FAMILY_CSS}" font-weight="400" font-size="${p.scaledDesc.toFixed(1)}" fill="rgb(0,0,0)">desc</text>`);
        trackX(leftEdge, leftEdge + p.width);
      }
      trackY(cursorY, cursorY + p.height);
      cursorY += p.height;
    }

    // BRAND NO aplica en esta variante, en ningún caso.
  } // fin renderSegundoPorFlow

  parts.push(`<image href="${template.background_data_url}" xlink:href="${template.background_data_url}" x="0" y="0" width="${cw}" height="${ch}"/>`);
  parts.push(...bodyParts);

  // Recorte al contenido real, con un pequeño margen de aire
  const PAD = Math.round(gap * 0.3);
  const crop = {
    left: Math.max(0, Math.floor(contentLeft - PAD)),
    top: Math.max(0, Math.floor(contentTop - PAD)),
    right: Math.min(cw, Math.ceil(contentRight + PAD)),
    bottom: Math.min(ch, Math.ceil(contentBottom + PAD)),
  };
  const cropWidth = crop.right - crop.left;
  const cropHeight = crop.bottom - crop.top;

  // Medidas SIN recortar (antes del Math.min contra cw/ch) — "crop" ya viene
  // siempre acotado al lienzo, así que comparar crop.width/height contra
  // cw/ch nunca detecta un desborde real (esa comparación es tautológica).
  // rawRight/rawBottom son las que hay que comparar para saber si el
  // contenido genuino se pasó y se cortó algo de verdad.
  const rawRight = Math.ceil(contentRight + PAD);
  const rawBottom = Math.ceil(contentBottom + PAD);

  const svgOpen = `<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="${cropWidth}" height="${cropHeight}" viewBox="${crop.left} ${crop.top} ${cropWidth} ${cropHeight}">`;
  const svgString = [svgOpen, ...defsParts, ...parts, "</svg>"].join("\n");

  return { svgString, crop: { ...crop, width: cropWidth, height: cropHeight, rawRight, rawBottom } };
}

/* =========================================================================
   4b. Construir el SVG — orientación HORIZONTAL
   ========================================================================= */
// A diferencia de la vertical (un solo flujo top-to-bottom, todo centrado en
// cw/2), la plantilla horizontal (extraída de 01H_PROMOSIMPLE_PRECIODECENAS,
// 29500x6466) es un layout de 3 zonas independientes, cada una centrada en
// SU PROPIA caja — no en el centro del lienzo completo:
//   - Izquierda: CALLOUT solo (una imagen, sin flujo).
//   - Centro: DESCRIPCION arriba, BRAND abajo (fluyen juntas, centradas en el
//     ancho de la caja de DESCRIPCION).
//   - Derecha: FRASE APOYO arriba, PESOS abajo (fluyen juntas, centradas en
//     el ancho de la caja de PESOS) — reusa renderPesosBlock, la MISMA
//     función que usa la vertical para el precio, solo que acotada a la caja
//     de PESOS en vez de a todo el lienzo.
// El punto de partida vertical (Y) de cada columna se lee siempre del
// template ORIGINAL sin escalar (nunca del mapping, que puede venir
// agrandado por buildSvgWithMaxFill): scaleMapping escala "y" como si fuera
// una simple diferencia (válido en la vertical, que nunca lo usa como
// posición absoluta — ver su propio comentario), así que usarlo tal cual acá
// alejaría cada columna del origen sin límite a medida que crece el
// contenido. Los anchos/altos de caja SÍ vienen del mapping (para que
// crezcan con el resto).
//
// Cubre por ahora los casos "normales" (precio/porcentaje/cantidad/
// combinado/doble — los mismos modos que ya calcula compute_pesos_field en
// el backend). Las variantes especiales (doble descripción, "segundo por",
// solo frase+descripción, solo descripción) todavía no tienen una referencia
// visual horizontal — si el Excel las dispara sobre una plantilla
// horizontal, se avisa por log y se dibuja solo con los campos base.
async function buildSvgHorizontal(template, mapping, rawExcelFields) {
  const cw = template.canvas.width, ch = template.canvas.height;
  const parts = [];
  const defsParts = [];

  if (template.fonts) {
    const fontFaces = Object.entries(template.fonts).map(([name, dataUrl]) => {
      const weight = weightForFontName(name);
      return `@font-face { font-family: 'Work Sans'; font-weight: ${weight}; src: url(${dataUrl}) format('woff2'); }`;
    }).join("\n");
    defsParts.push(`<defs><style>${fontFaces}</style></defs>`);
  }

  const fieldsByColumn = Object.fromEntries(mapping.map((f) => [f.excel_column, f]));

  /** Caja de diseño ORIGINAL (sin escalar) de un campo — ver nota arriba
   *  sobre por qué la posición Y de cada columna se lee de acá y no del
   *  mapping (posiblemente ya agrandado). */
  function originalBox(excelColumn) {
    const norm = normalize(excelColumn);
    const tf = (template.fields || []).find((f) => normalize(f.excel_column) === norm);
    return tf ? tf.box : null;
  }
  function originalBoxY(excelColumn) {
    const b = originalBox(excelColumn);
    return b ? b.y : 0;
  }

  // --- Ancho MÁXIMO por columna (calculado una sola vez sobre las cajas
  // ORIGINALES, nunca sobre el mapping ya escalado) ---
  // buildSvgWithMaxFill (reusado tal cual de la vertical) agranda ancho +
  // alto de cada campo por el MISMO factor para "aprovechar el espacio", sin
  // saber que acá hay 3 columnas independientes una al lado de la otra: sin
  // este tope, una descripción larga podía crecer tanto de ancho que invadía
  // el texto de la columna vecina (visto en pruebas: "2x$45" pisando el
  // final de la descripción). Como el CENTRO de cada campo de texto queda
  // siempre estable pase lo que pase (scaleMapping preserva el centro
  // original al escalar box.x), alcanza con topear cuánto puede crecer el
  // ANCHO usado para armar/ajustar el texto — el resto de la lógica de
  // "aprovechar espacio" (tamaño de fuente, etc.) sigue igual.
  const COLUMN_SAFETY_MARGIN = cw * 0.01; // ~1% del ancho del lienzo, aire mínimo entre columnas
  function maxCenteredWidth(centerX, leftEdge, rightEdge) {
    const halfWidth = Math.min(centerX - leftEdge, rightEdge - centerX) - COLUMN_SAFETY_MARGIN;
    return Math.max(1, halfWidth * 2);
  }
  const origCallout = originalBox("CALLOUT");
  const origDescripcion = originalBox("DESCRIPCION");
  const origFraseApoyo = originalBox("FRASE APOYO");
  const origPesos = originalBox("PESOS");

  const maxDescripcionWidth = maxCenteredWidth(
    origDescripcion.x + origDescripcion.width / 2,
    origCallout ? origCallout.x + origCallout.width : 0,
    origFraseApoyo.x
  );
  const maxFraseApoyoWidth = maxCenteredWidth(
    origFraseApoyo.x + origFraseApoyo.width / 2,
    origDescripcion.x + origDescripcion.width,
    cw
  );
  const maxPesosWidth = maxCenteredWidth(
    origPesos.x + origPesos.width / 2,
    origDescripcion.x + origDescripcion.width,
    cw
  );
  // CALLOUT/BRAND son imágenes ancladas por box.x (no centradas) — se topa
  // el ANCHO (el alto sigue igual) para que su borde derecho no invada la
  // descripción, con el mismo margen de seguridad.
  const maxCalloutWidth = Math.max(1, origDescripcion.x - (origCallout ? origCallout.x : 0) - COLUMN_SAFETY_MARGIN);

  let contentTop = null, contentBottom = null;
  let contentLeft = cw, contentRight = 0;
  function trackX(left, right) {
    contentLeft = Math.min(contentLeft, left);
    contentRight = Math.max(contentRight, right);
  }
  // A diferencia de la vertical (un solo flujo, siempre creciente — ahí alcanza
  // con recordar el primer top y el último bottom), acá hay 3 columnas
  // independientes que se dibujan en paralelo: hay que tomar el mínimo de los
  // tops y el máximo de los bottoms vistos, no importa el orden.
  function trackY(top, bottom) {
    contentTop = contentTop === null ? top : Math.min(contentTop, top);
    contentBottom = contentBottom === null ? bottom : Math.max(contentBottom, bottom);
  }

  function isPresent(field) {
    return !!(field && field.matched && field.value && String(field.value).trim() !== "");
  }

  const bodyParts = [];

  const callout = fieldsByColumn["CALLOUT"];
  const descripcion = fieldsByColumn["DESCRIPCION"];
  const brand = fieldsByColumn["BRAND"];
  const fraseApoyo = fieldsByColumn["FRASE APOYO"];
  const pesos = fieldsByColumn["PESOS"];

  function rawFieldByColumn(columnName) {
    const norm = normalize(columnName);
    return (rawExcelFields || []).find((f) => normalize(f.column) === norm) || null;
  }

  // --- Detección de variantes especiales (12-17) — mismas señales/reglas que
  // la vertical (el backend las calcula igual, sin importar la orientación:
  // ver backend/main.py). Lo único que cambia acá es CÓMO se dibujan: en vez
  // de un solo flujo que se apila de arriba a abajo (como en buildSvg), se
  // reparte en "bloques" que se acomodan de izquierda a derecha a lo ancho
  // del lienzo, cada uno centrado verticalmente — el mismo contenido y la
  // misma mecánica (medir todo antes de dibujar, banners fijos reutilizados,
  // presupuesto de alto para que nunca se desborde), solo que la dirección
  // en la que se van acomodando los elementos es horizontal, no vertical. ---
  const doubleDescField = rawFieldByColumn("PROMO_DOBLE_DESCRIPCION");
  const doubleDescData = doubleDescField ? doubleDescField.doble_desc : null;
  const segundoPorField = rawFieldByColumn("PROMO_SEGUNDO_POR");
  const segundoPorData = segundoPorField ? segundoPorField.segundo_por : null;
  const soloFraseDescField = rawFieldByColumn("PROMO_SOLO_FRASE_DESC");
  const soloFraseDescData = soloFraseDescField ? soloFraseDescField.solo_frase_desc : null;
  const soloDescripcionField = rawFieldByColumn("PROMO_SOLO_DESCRIPCION");
  const soloDescripcionData = soloDescripcionField ? soloDescripcionField.solo_descripcion : null;

  const GAP_MULTIPLIER = 1.15;
  const midGapRaw = (descripcion && brand) ? brand.box.y - (descripcion.box.y + descripcion.box.height) : null;
  const rightGapRaw = (fraseApoyo && pesos) ? pesos.box.y - (fraseApoyo.box.y + fraseApoyo.box.height) : null;
  const gap = (midGapRaw != null ? midGapRaw : (rightGapRaw != null ? rightGapRaw : 187)) * GAP_MULTIPLIER;

  // ===========================================================================
  // Helpers compartidos por las variantes especiales (12-17): reparto de
  // "slots" (bloques) de izquierda a derecha, y medición de una línea de texto
  // sin dibujar nada (igual que fitFontSizeToWidth, pero devolviendo también
  // el alto, para poder centrar verticalmente antes de dibujar).
  // ===========================================================================
  const SLOT_GAP = cw * 0.02;
  function layoutSlots(weights) {
    const margin = cw * 0.02;
    const totalGap = SLOT_GAP * Math.max(0, weights.length - 1);
    const available = Math.max(1, cw - 2 * margin - totalGap);
    const totalWeight = weights.reduce((a, b) => a + b, 0) || 1;
    const slots = [];
    let x = margin;
    for (const w of weights) {
      const width = available * (w / totalWeight);
      slots.push({ x, width, centerX: x + width / 2 });
      x += width + SLOT_GAP;
    }
    return slots;
  }
  function measureSingleLine(text, weight, baseFontSize, maxWidth) {
    const fittedSize = fitFontSizeToWidth(text, weight, baseFontSize, maxWidth);
    const width = measureTextWidth(text, weight, fittedSize);
    const height = paragraphHeight(1, fittedSize, text);
    return { fittedSize, width, height };
  }

  /** Bloque "CALLOUT" reutilizado por las variantes especiales: imagen
   * contenida dentro del slot, centrada tanto horizontal como verticalmente
   * (a diferencia de la columna izquierda del flujo normal, que ancla su
   * posición Y original del PSD — acá no hay una posición de referencia para
   * este layout nuevo, así que se centra en el lienzo). */
  async function renderCalloutSlot(calloutField, slot) {
    const dataUrl = await fetchDynamicAssetDataUrl(calloutField.value);
    const natural = await loadImageNaturalSize(dataUrl);
    const disp = containFit(natural.w, natural.h, slot.width, ch * 0.55);
    const x = slot.centerX - disp.w / 2;
    const y = (ch - disp.h) / 2;
    bodyParts.push(`<image href="${dataUrl}" xlink:href="${dataUrl}" x="${x}" y="${y}" width="${disp.w}" height="${disp.h}" preserveAspectRatio="xMidYMid meet"/>`);
    trackX(x, x + disp.w);
    trackY(y, y + disp.h);
  }

  /** Bloque "FRASE APOYO" (opcional) + "COMPRA" (fijo, siempre) apilados y
   * centrados como grupo dentro del slot — mismo texto/estilo fijo que usa
   * la vertical (fuente/tamaño heredados de FRASE APOYO, con "WorkSans-Black"
   * / 727 como default si no hay FRASE APOYO en la plantilla). */
  function renderFraseCompraSlot(slot) {
    const compraFont = fraseApoyo ? fraseApoyo.font_family : "WorkSans-Black";
    const compraSize = fraseApoyo ? fraseApoyo.font_size : 727;
    const compraWeight = weightForFontName(compraFont);
    const compraText = "COMPRA";
    const compraM = measureSingleLine(compraText, compraWeight, compraSize, slot.width * 0.95);

    let fraseM = null, fraseWeight = 0, plainFrase = "", fraseRuns = null;
    if (isPresent(fraseApoyo)) {
      fraseWeight = weightForFontName(fraseApoyo.font_family);
      fraseRuns = fraseApoyo.runs && fraseApoyo.runs.length ? fraseApoyo.runs : [{ text: fraseApoyo.value }];
      plainFrase = fraseRuns.map((r) => r.text).join("");
      fraseM = measureSingleLine(plainFrase, fraseWeight, fraseApoyo.font_size, slot.width * 0.95);
    }

    const innerGap = gap * 0.5;
    const totalHeight = (fraseM ? fraseM.height + innerGap : 0) + compraM.height;
    let y = (ch - totalHeight) / 2;

    if (fraseM) {
      const baselineY = y + fraseM.fittedSize * ascentRatioFor(plainFrase);
      bodyParts.push(lineToSvgText(runsToWords(fraseRuns), fraseWeight, fraseM.fittedSize, fraseApoyo.color, slot.centerX, baselineY));
      trackX(slot.centerX - fraseM.width / 2, slot.centerX + fraseM.width / 2);
      trackY(y, y + fraseM.height);
      y += fraseM.height + innerGap;
    }
    {
      const baselineY = y + compraM.fittedSize * ascentRatioFor(compraText);
      bodyParts.push(`<text x="${slot.centerX}" y="${baselineY}" text-anchor="middle" font-family="${FONT_FAMILY_CSS}" font-weight="${compraWeight}" font-size="${compraM.fittedSize.toFixed(1)}" fill="rgb(0,0,0)">${escapeXml(compraText)}</text>`);
      trackX(slot.centerX - compraM.width / 2, slot.centerX + compraM.width / 2);
      trackY(y, y + compraM.height);
    }
  }

  /** Párrafo (DESCRIPCION / DESCRIPCION 2) centrado vertical y horizontalmente
   * dentro de su slot — misma fitParagraph de siempre, solo que acá el ancho
   * de ajuste es el del slot (no una caja fija del PSD) y se centra en Y
   * porque no hay un flujo compartido con el resto de los bloques. */
  function renderParagraphSlot(field, slot, maxLines) {
    if (!isPresent(field)) return;
    const weight = weightForFontName(field.font_family);
    const runs = field.runs && field.runs.length ? field.runs : [{ text: field.value }];
    const maxHeight = ch * 0.85;
    const { fontSize: fittedSize, lines, actualHeight, maxLineWidth } =
      fitParagraph(runs, weight, field.font_size, slot.width * 0.95, maxHeight, maxLines);
    const plainText = lines.map((l) => l.map((w) => w.text).join(" ")).join(" ");
    const firstLineAscent = ascentRatioFor(plainText);
    const y = (ch - actualHeight) / 2;
    lines.forEach((line, i) => {
      const baselineY = y + fittedSize * firstLineAscent + i * fittedSize * LINE_HEIGHT_RATIO;
      bodyParts.push(lineToSvgText(line, weight, fittedSize, field.color, slot.centerX, baselineY));
    });
    trackX(slot.centerX - maxLineWidth / 2, slot.centerX + maxLineWidth / 2);
    trackY(y, y + actualHeight);
  }

  // --- Flujo normal (casos 1-11, y 16 "solo frase y descripción" con hasta 8
  // líneas) — 3 columnas independientes ancladas a las posiciones del PSD,
  // como ya estaba. ---
  async function renderNormalFlowHorizontal(descripcionMaxLines = 4) {

  // --- Columna izquierda: CALLOUT, elemento único, sin flujo ---
  // Alineado a la DERECHA dentro de su caja (preserveAspectRatio="xMaxYMid",
  // no "xMidYMid") y con la caja centrada verticalmente en el lienzo. Antes,
  // centrado dentro de una caja ancha, un callout angosto (ej. una insignia
  // corta como "NUEVO") dejaba un hueco vacío de más A SU DERECHA — el
  // espacio visual hasta la columna de DESCRIPCION se veía más grande que en
  // una fila con un callout que sí llena su ancho, rompiendo la sensación de
  // espaciado uniforme entre filas. Alineando a la derecha, ese espacio
  // queda SIEMPRE igual (la distancia de la caja al inicio de DESCRIPCION es
  // fija, viene del PSD), sin importar la proporción de la imagen real.
  //
  // OJO: el borde derecho de la caja tiene que quedar en una posición FIJA
  // (origCallout.x + maxCalloutWidth — la misma cuenta que ya usa
  // maxCalloutWidth para no invadir DESCRIPCION), NO en "callout.box.x +
  // width". "callout.box.x" es la posición YA ESCALADA por scaleMapping
  // (crece de forma simétrica alrededor del centro ORIGINAL para "aprovechar
  // espacio", así que se corre fila a fila según cuánto haya crecido esta
  // fila en particular) y "width" puede venir topada por maxCalloutWidth —
  // ninguna de las dos por separado garantiza un borde derecho constante.
  // Usar directamente "callout.box.x + width" (como se hacía antes) hacía
  // que el espacio entre el callout y DESCRIPCION variara de fila a fila
  // (más marcado cuando el ancho quedaba topado, como con CA_TUELIGES.svg,
  // de proporción más ancha) — reportado por el usuario comparando varias
  // filas generadas: en unas el espaciado se veía parejo, en otras no.
  if (isPresent(callout)) {
    const width = Math.min(callout.box.width, maxCalloutWidth);
    const rightEdge = (origCallout ? origCallout.x : 0) + maxCalloutWidth;
    const x = rightEdge - width;
    const y = (ch - callout.box.height) / 2;
    const dataUrl = await fetchDynamicAssetDataUrl(callout.value, "xMaxYMid meet");
    const natural = await loadImageNaturalSize(dataUrl);
    const disp = containFit(natural.w, natural.h, width, callout.box.height);
    const dispLeft = x + width - disp.w;
    bodyParts.push(`<image href="${dataUrl}" xlink:href="${dataUrl}" x="${x}" y="${y}" width="${width}" height="${callout.box.height}" preserveAspectRatio="xMaxYMid meet"/>`);
    trackX(dispLeft, dispLeft + disp.w);
    trackY(y, y + callout.box.height);
  }

  // --- Columna central: DESCRIPCION -> BRAND, centrada como BLOQUE en el
  // alto del lienzo (no anclada a la posición Y fija del PSD) — así, sin
  // importar si viene BRAND o no, o cuántas líneas use la descripción, el
  // conjunto queda alineado con las otras 2 columnas (que se centran igual),
  // en vez de arrancar siempre en la misma Y fija y dejar un hueco dispar
  // abajo cuando el contenido real es más corto que el diseño original. ---
  {
    const weight = isPresent(descripcion) ? weightForFontName(descripcion.font_family) : null;
    let fittedSize = 0, lines = [], actualHeight = 0, maxLineWidth = 0, centerX = 0, plainText = "";
    if (isPresent(descripcion)) {
      const runs = descripcion.runs && descripcion.runs.length ? descripcion.runs : [{ text: descripcion.value }];
      centerX = descripcion.box.x + descripcion.box.width / 2;
      // El multiplicador (descripcionMaxLines/4) sube el TECHO permitido para
      // el caso de 8 líneas (variante "solo frase y descripción"), pero ese
      // techo nunca puede pasarse del espacio que REALMENTE cabe en el
      // lienzo — si no, con canvas horizontales más bajos que el vertical
      // (6466px vs 8000px) el doble de alto empuja a BRAND fuera del lienzo
      // (bug real encontrado al probar el caso 16H). 0.98 dejando un 2% de
      // aire total (arriba+abajo, ya que el bloque queda centrado).
      const bottomReserve = isPresent(brand) ? gap + brand.box.height : 0;
      const availableHeight = Math.max(descripcion.box.height * 0.4, ch * 0.98 - bottomReserve);
      const descripcionMaxHeight = Math.min(
        descripcion.box.height * (descripcionMaxLines / 4) * 0.97,
        availableHeight
      );
      const wrapWidth = Math.min(descripcion.box.width, maxDescripcionWidth);
      ({ fontSize: fittedSize, lines, actualHeight, maxLineWidth } =
        fitParagraph(runs, weight, descripcion.font_size, wrapWidth, descripcionMaxHeight, descripcionMaxLines));
      plainText = lines.map((l) => l.map((w) => w.text).join(" ")).join(" ");
    }

    const brandHeight = isPresent(brand) ? brand.box.height : 0;
    const middleTotalHeight = actualHeight + (isPresent(descripcion) && isPresent(brand) ? gap : 0) + brandHeight;
    let midY = (ch - middleTotalHeight) / 2;

    if (isPresent(descripcion)) {
      const firstLineAscent = ascentRatioFor(plainText);
      lines.forEach((line, i) => {
        const baselineY = midY + fittedSize * firstLineAscent + i * fittedSize * LINE_HEIGHT_RATIO;
        bodyParts.push(lineToSvgText(line, weight, fittedSize, descripcion.color, centerX, baselineY));
      });
      trackX(centerX - maxLineWidth / 2, centerX + maxLineWidth / 2);
      trackY(midY, midY + actualHeight);
      midY += actualHeight + (isPresent(brand) ? gap : 0);
    }

    if (isPresent(brand)) {
      // BRAND vive en la misma columna que DESCRIPCION — se topa con el mismo
      // ancho máximo, centrado sobre SU PROPIO centro (que puede diferir un
      // poco del de DESCRIPCION, ver template.json).
      const brandCenterX = brand.box.x + brand.box.width / 2;
      const width = Math.min(brand.box.width, maxDescripcionWidth);
      const x = brandCenterX - width / 2;
      const dataUrl = await fetchDynamicAssetDataUrl(brand.value);
      const natural = await loadImageNaturalSize(dataUrl);
      const disp = containFit(natural.w, natural.h, width, brand.box.height);
      const dispLeft = x + (width - disp.w) / 2;
      bodyParts.push(`<image href="${dataUrl}" xlink:href="${dataUrl}" x="${x}" y="${midY}" width="${width}" height="${brand.box.height}" preserveAspectRatio="xMidYMid meet"/>`);
      trackX(dispLeft, dispLeft + disp.w);
      trackY(midY, midY + brand.box.height);
    }
  }

  // --- Columna derecha: FRASE APOYO -> PESOS, centrada como BLOQUE en el
  // alto del lienzo — mismo criterio que la columna central. ---
  {
    let fraseWeight = 0, fraseFittedSize = 0, fraseLineWidth = 0, fraseLineHeight = 0, fraseCenterX = 0, frasePlainText = "", fraseRuns = null;
    if (isPresent(fraseApoyo)) {
      fraseWeight = weightForFontName(fraseApoyo.font_family);
      fraseRuns = fraseApoyo.runs && fraseApoyo.runs.length ? fraseApoyo.runs : [{ text: fraseApoyo.value }];
      frasePlainText = fraseRuns.map((r) => r.text).join("");
      fraseCenterX = fraseApoyo.box.x + fraseApoyo.box.width / 2;
      const maxWidth = Math.min(fraseApoyo.box.width, maxFraseApoyoWidth) * 0.95;
      fraseFittedSize = fitFontSizeToWidth(frasePlainText, fraseWeight, fraseApoyo.font_size, maxWidth);
      fraseLineWidth = measureTextWidth(frasePlainText, fraseWeight, fraseFittedSize);
      fraseLineHeight = paragraphHeight(1, fraseFittedSize, frasePlainText);
    }

    let pesosWeight = 0, pesosPrefixWeight = 0, pesosCenterX = 0, pesosMaxWidth = 0, pesosHeight = 0;
    if (isPresent(pesos)) {
      pesosWeight = weightForFontName(pesos.font_family);
      pesosPrefixWeight = pesos.prefix ? weightForFontName(pesos.prefix.font_family) : pesosWeight;
      pesosCenterX = pesos.box.x + pesos.box.width / 2;
      pesosMaxWidth = Math.min(pesos.box.width, maxPesosWidth) * 0.95;
      // renderPesosBlock siempre DIBUJA — se llama una vez "al vacío" (con un
      // bodyParts descartable y trackX/trackY sin efecto), empezando en
      // startY=0, solo para saber cuánto alto real va a usar (el cursorY que
      // devuelve, al haber arrancado en 0, ES ese alto) — recién con ese dato
      // se puede calcular la Y centrada, y ahí sí se llama de nuevo para el
      // dibujo definitivo.
      pesosHeight = renderPesosBlock(pesos, pesosWeight, pesosPrefixWeight, pesosCenterX, pesosMaxWidth, 0, [], () => {}, () => {});
    }

    // Mismo criterio que siempre: el gap justo antes del precio se reduce un
    // poco — su fórmula de alto reserva bastante espacio de ascenso por la
    // fuente enorme, así que con el gap completo se veía más separado de
    // FRASE APOYO que el resto de las transiciones. Empezó en 0.75x; el
    // usuario pidió bajarlo otro 35% adicional (0.75 * 0.65 = 0.4875x) por
    // seguir viéndose grande.
    const PESOS_GAP_FACTOR = 0.4875;
    const rightTotalHeight = fraseLineHeight
      + (isPresent(fraseApoyo) && isPresent(pesos) ? gap * PESOS_GAP_FACTOR : 0)
      + pesosHeight;
    let rightY = (ch - rightTotalHeight) / 2;

    if (isPresent(fraseApoyo)) {
      const baselineY = rightY + fraseFittedSize * ascentRatioFor(frasePlainText);
      bodyParts.push(lineToSvgText(runsToWords(fraseRuns), fraseWeight, fraseFittedSize, fraseApoyo.color, fraseCenterX, baselineY));
      trackX(fraseCenterX - fraseLineWidth / 2, fraseCenterX + fraseLineWidth / 2);
      trackY(rightY, rightY + fraseLineHeight);
      rightY += fraseLineHeight + (isPresent(pesos) ? gap * PESOS_GAP_FACTOR : 0);
    }

    if (isPresent(pesos)) {
      renderPesosBlock(pesos, pesosWeight, pesosPrefixWeight, pesosCenterX, pesosMaxWidth, rightY, bodyParts, trackX, trackY);
    }
  }

  } // fin renderNormalFlowHorizontal

  // --- Caso 17 "solo descripción": SIN CALLOUT/BRAND/FRASE APOYO/COMPRA en
  // ningún caso — únicamente la descripción, centrada en el lienzo completo,
  // hasta 20 líneas. Es el caso más simple de portar: en la vertical ya es
  // "un solo bloque centrado", así que la única diferencia real es el
  // ancho/alto del lienzo (mismas proporciones, mismo criterio de ajuste). ---
  async function renderOnlyDescriptionFlowHorizontal() {
    if (!isPresent(descripcion)) return;
    const weight = weightForFontName(descripcion.font_family);
    const runs = descripcion.runs && descripcion.runs.length ? descripcion.runs : [{ text: descripcion.value }];
    const wrapWidth = cw * 0.85;
    const maxHeight = ch * 0.85;
    const { fontSize: fittedSize, lines, actualHeight, maxLineWidth } =
      fitParagraph(runs, weight, descripcion.font_size, wrapWidth, maxHeight, 20);
    const plainText = lines.map((l) => l.map((w) => w.text).join(" ")).join(" ");
    const firstLineAscent = ascentRatioFor(plainText);
    const startY = (ch - actualHeight) / 2;
    lines.forEach((line, i) => {
      const baselineY = startY + fittedSize * firstLineAscent + i * fittedSize * LINE_HEIGHT_RATIO;
      bodyParts.push(lineToSvgText(line, weight, fittedSize, descripcion.color, cw / 2, baselineY));
    });
    trackX((cw - maxLineWidth) / 2, (cw + maxLineWidth) / 2);
    trackY(startY, startY + actualHeight);
  }

  // --- Casos 12/13 "doble descripción" (con/sin precio): mismo contenido y
  // mecánica que la vertical (COMPRA fijo + descripción 1 + [precio+banner
  // LLÉVATE | banner Y LLÉVATE+GRATIS] + descripción 2), pero en vez de
  // apilarse de arriba a abajo en una sola columna, se reparte en bloques de
  // izquierda a derecha — el mismo diferencial de orientación que ya se usa
  // en el flujo normal (3 columnas), aplicado a este set de contenido nuevo. ---
  async function renderDoubleDescriptionFlowHorizontal() {
    const descripcion1 = descripcion; // misma columna de siempre = descripción 1
    const descripcion2Field = rawFieldByColumn("DESCRIPCION 2");
    const showCallout = doubleDescData.case === 2 && isPresent(callout);

    const slotDefs = [];
    if (showCallout) slotDefs.push("callout");
    slotDefs.push("frase_compra");
    slotDefs.push("desc1");
    slotDefs.push("middle");
    if (descripcion2Field && isPresent(descripcion1)) slotDefs.push("desc2");
    const weights = slotDefs.map((t) => (t === "desc1" || t === "desc2" ? 1.3 : 1));
    const slots = layoutSlots(weights);

    for (let i = 0; i < slotDefs.length; i++) {
      const type = slotDefs[i];
      const slot = slots[i];

      if (type === "callout") {
        await renderCalloutSlot(callout, slot);
      } else if (type === "frase_compra") {
        renderFraseCompraSlot(slot);
      } else if (type === "desc1") {
        renderParagraphSlot(descripcion1, slot, 6);
      } else if (type === "desc2") {
        renderParagraphSlot({ ...descripcion1, value: descripcion2Field.value, runs: descripcion2Field.runs }, slot, 6);
      } else if (type === "middle") {
        if (doubleDescData.case === 1) {
          // "+$NN" (máx. 2 cifras) + banner fijo LLÉVATE, apilados y centrados.
          const dollarText = pesos && pesos.prefix ? pesos.prefix.text : "$";
          const priceColor = doubleDescData.price_color || (pesos ? pesos.color : "rgb(220,39,38)");
          const priceWeight = pesos ? weightForFontName(pesos.font_family) : 900;
          const priceTextVal = doubleDescData.price_integer;
          const smallRatio = pesos && pesos.prefix ? (pesos.prefix.font_size / pesos.font_size) : 0.47;
          const baseSize = pesos ? pesos.font_size : 2400;
          const mainSize = fitFontSizeToWidth("+" + dollarText + priceTextVal, priceWeight, baseSize, slot.width * 0.95);
          const smallSize = mainSize * smallRatio;
          const shift = ((mainSize - smallSize) * 0.72) / 2;
          const priceLineWidth = measureTextWidth("+", priceWeight, smallSize) + measureTextWidth(dollarText, priceWeight, smallSize) + measureTextWidth(priceTextVal, priceWeight, mainSize);
          const priceLineHeight = paragraphHeight(1, mainSize, priceTextVal);

          const bannerDataUrl = await fetchAssetDataUrl("CAFIJO_LLEVATE.svg");
          const natural = await loadImageNaturalSize(bannerDataUrl);
          const disp = containFit(natural.w, natural.h, slot.width, ch * 0.4);

          const innerGap = gap * 0.5;
          let y = (ch - (priceLineHeight + innerGap + disp.h)) / 2;

          const baselineY = y + mainSize * ascentRatioFor(priceTextVal);
          const tspans =
            `<tspan fill="rgb(0,0,0)" font-size="${smallSize.toFixed(1)}" baseline-shift="${shift.toFixed(1)}">+</tspan>` +
            `<tspan fill="${priceColor}" font-size="${smallSize.toFixed(1)}" baseline-shift="${shift.toFixed(1)}">${escapeXml(dollarText)}</tspan>` +
            `<tspan fill="${priceColor}" font-size="${mainSize.toFixed(1)}">${escapeXml(priceTextVal)}</tspan>`;
          bodyParts.push(`<text x="${slot.centerX}" y="${baselineY}" text-anchor="middle" font-family="${FONT_FAMILY_CSS}" font-weight="${priceWeight}">${tspans}</text>`);
          trackX(slot.centerX - priceLineWidth / 2, slot.centerX + priceLineWidth / 2);
          trackY(y, y + priceLineHeight);
          y += priceLineHeight + innerGap;

          const bannerX = slot.centerX - disp.w / 2;
          bodyParts.push(`<image href="${bannerDataUrl}" xlink:href="${bannerDataUrl}" x="${bannerX}" y="${y}" width="${disp.w}" height="${disp.h}" preserveAspectRatio="xMidYMid meet"/>`);
          trackX(bannerX, bannerX + disp.w);
          trackY(y, y + disp.h);
        } else {
          // banner fijo Y LLÉVATE + "GRATIS" (negrilla), lado a lado.
          const bannerDataUrl = await fetchAssetDataUrl("CAFIJO_Y_LLEVATE.svg");
          const natural = await loadImageNaturalSize(bannerDataUrl);
          let disp = containFit(natural.w, natural.h, slot.width * 0.65, ch * 0.35);
          const gratisWeight = fraseApoyo ? weightForFontName(fraseApoyo.font_family) : 900;
          let gratisFontSize = disp.h * 0.9;
          const gratisText = "GRATIS";
          let gratisWidth = measureTextWidth(gratisText, Math.max(gratisWeight, 700), gratisFontSize);
          let gapBetween = disp.h * 0.15;
          let totalWidth = disp.w + gapBetween + gratisWidth;
          // El tamaño de "GRATIS" se deriva del alto del banner, no del ancho
          // disponible — con un slot angosto (ej. compartiendo espacio con
          // DESCRIPCION 2) el conjunto podía pasarse del slot e invadir el
          // texto vecino (bug real encontrado al probar el caso 13H "sin
          // ninguno"). Si no entra, se reescala TODO el conjunto (banner +
          // texto) en la misma proporción, para no romper su relación visual.
          const maxTotalWidth = slot.width * 0.95;
          if (totalWidth > maxTotalWidth) {
            const scale = maxTotalWidth / totalWidth;
            disp = { w: disp.w * scale, h: disp.h * scale };
            gratisFontSize *= scale;
            gratisWidth *= scale;
            gapBetween *= scale;
            totalWidth = maxTotalWidth;
          }
          const bannerLeft = slot.centerX - totalWidth / 2;
          const gratisLeft = bannerLeft + disp.w + gapBetween;
          const y = (ch - disp.h) / 2;
          bodyParts.push(`<image href="${bannerDataUrl}" xlink:href="${bannerDataUrl}" x="${bannerLeft}" y="${y}" width="${disp.w}" height="${disp.h}" preserveAspectRatio="xMidYMid meet"/>`);
          const gratisBaselineY = y + disp.h * ascentRatioFor(gratisText);
          bodyParts.push(`<text x="${gratisLeft}" y="${gratisBaselineY}" text-anchor="start" font-family="${FONT_FAMILY_CSS}" font-weight="${Math.max(gratisWeight, 700)}" font-size="${gratisFontSize.toFixed(1)}" fill="rgb(0,0,0)">${escapeXml(gratisText)}</text>`);
          trackX(bannerLeft, gratisLeft + gratisWidth);
          trackY(y, y + disp.h);
        }
      }
    }
  } // fin renderDoubleDescriptionFlowHorizontal

  // --- Casos 14/15 "y llévate el segundo (por/con)": mismo contenido que la
  // vertical (CALLOUT opcional + COMPRA + descripción + banner Y LLÉVATE +
  // "EL SEGUNDO POR/CON" + precio o porcentaje), repartido en bloques de
  // izquierda a derecha en vez de apilado de arriba a abajo. Sin BRAND en
  // ningún caso (igual que la vertical). ---
  async function renderSegundoPorFlowHorizontal() {
    const showCallout = isPresent(callout);

    const slotDefs = [];
    if (showCallout) slotDefs.push("callout");
    slotDefs.push("frase_compra");
    slotDefs.push("desc");
    slotDefs.push("second_group");
    const weights = slotDefs.map((t) => (t === "desc" ? 1.4 : (t === "second_group" ? 1.2 : 1)));
    const slots = layoutSlots(weights);

    for (let i = 0; i < slotDefs.length; i++) {
      const type = slotDefs[i];
      const slot = slots[i];

      if (type === "callout") {
        await renderCalloutSlot(callout, slot);
      } else if (type === "frase_compra") {
        renderFraseCompraSlot(slot);
      } else if (type === "desc") {
        renderParagraphSlot(descripcion, slot, 4);
      } else if (type === "second_group") {
        const bannerDataUrl = await fetchAssetDataUrl("CAFIJO_Y_LLEVATE.svg");
        const natural = await loadImageNaturalSize(bannerDataUrl);
        const disp = containFit(natural.w, natural.h, slot.width, ch * 0.32);

        const elSegundoText = segundoPorData.case === 1 ? "EL SEGUNDO POR" : "EL SEGUNDO CON";
        const elSegundoWeight = fraseApoyo ? weightForFontName(fraseApoyo.font_family) : 900;
        const elSegundoBaseSize = fraseApoyo ? fraseApoyo.font_size : 727;
        const elM = measureSingleLine(elSegundoText, elSegundoWeight, elSegundoBaseSize, slot.width * 0.95);

        let priceHeight, priceDraw;
        if (segundoPorData.case === 1) {
          const dollarText = pesos && pesos.prefix ? pesos.prefix.text : "$";
          const priceColor = segundoPorData.price_color || (pesos ? pesos.color : "rgb(220,39,38)");
          const priceWeight = pesos ? weightForFontName(pesos.font_family) : 900;
          const priceTextVal = segundoPorData.price_integer;
          const smallRatio = pesos && pesos.prefix ? (pesos.prefix.font_size / pesos.font_size) : 0.47;
          const baseSize = pesos ? pesos.font_size * 0.45 : 1200;
          const mainSize = fitFontSizeToWidth(dollarText + priceTextVal, priceWeight, baseSize, slot.width * 0.95);
          const smallSize = mainSize * smallRatio;
          const shift = ((mainSize - smallSize) * 0.72) / 2;
          const lineWidth = measureTextWidth(dollarText, priceWeight, smallSize) + measureTextWidth(priceTextVal, priceWeight, mainSize);
          priceHeight = paragraphHeight(1, mainSize, priceTextVal);
          priceDraw = (y) => {
            const baselineY = y + mainSize * ascentRatioFor(priceTextVal);
            const tspans =
              `<tspan fill="${priceColor}" font-size="${smallSize.toFixed(1)}" baseline-shift="${shift.toFixed(1)}">${escapeXml(dollarText)}</tspan>` +
              `<tspan fill="${priceColor}" font-size="${mainSize.toFixed(1)}">${escapeXml(priceTextVal)}</tspan>`;
            bodyParts.push(`<text x="${slot.centerX}" y="${baselineY}" text-anchor="middle" font-family="${FONT_FAMILY_CSS}" font-weight="${priceWeight}">${tspans}</text>`);
            trackX(slot.centerX - lineWidth / 2, slot.centerX + lineWidth / 2);
            trackY(y, y + priceHeight);
          };
        } else {
          const numberText = segundoPorData.percent_number;
          const weight = pesos ? weightForFontName(pesos.font_family) : 900;
          const color = segundoPorData.price_color || (pesos ? pesos.color : "rgb(220,39,38)");
          const baseSize = pesos ? pesos.font_size * 0.45 : 1200;
          const mainSize = fitFontSizeToWidth(numberText + "%", weight, baseSize, slot.width * 0.7);
          const percentFontSize = mainSize * 0.5;
          const percentWidthAtBase = measureTextWidth("%", weight, percentFontSize);
          const descWidthAtPercentSize = measureTextWidth("desc", weight, percentFontSize);
          const descFontSize = percentFontSize * (percentWidthAtBase / descWidthAtPercentSize);
          const gapX = mainSize * 0.06;
          const numberWidth = measureTextWidth(numberText, weight, mainSize);
          const stackWidth = percentWidthAtBase;
          const lineWidth = numberWidth + gapX + stackWidth;
          priceHeight = paragraphHeight(1, mainSize, numberText);
          priceDraw = (y) => {
            const baselineY = y + mainSize * ascentRatioFor(numberText);
            const leftEdge = slot.centerX - lineWidth / 2;
            const numberEndX = leftEdge + numberWidth;
            const stackLeftX = numberEndX + gapX;
            bodyParts.push(`<text x="${numberEndX}" y="${baselineY}" text-anchor="end" font-family="${FONT_FAMILY_CSS}" font-weight="${weight}" font-size="${mainSize.toFixed(1)}" fill="${color}">${escapeXml(numberText)}</text>`);
            const descCapHeight = descFontSize * 0.72;
            const smallGap = mainSize * 0.03;
            const percentBaselineY = baselineY - descCapHeight - smallGap;
            bodyParts.push(`<text x="${stackLeftX}" y="${percentBaselineY}" text-anchor="start" font-family="${FONT_FAMILY_CSS}" font-weight="${weight}" font-size="${percentFontSize.toFixed(1)}" fill="${color}">%</text>`);
            bodyParts.push(`<text x="${stackLeftX}" y="${baselineY}" text-anchor="start" font-family="${FONT_FAMILY_CSS}" font-weight="400" font-size="${descFontSize.toFixed(1)}" fill="rgb(0,0,0)">desc</text>`);
            trackX(leftEdge, leftEdge + lineWidth);
            trackY(y, y + priceHeight);
          };
        }

        const innerGap = gap * 0.4;
        const totalHeight = disp.h + innerGap + elM.height + innerGap + priceHeight;
        let y = (ch - totalHeight) / 2;

        const bannerX = slot.centerX - disp.w / 2;
        bodyParts.push(`<image href="${bannerDataUrl}" xlink:href="${bannerDataUrl}" x="${bannerX}" y="${y}" width="${disp.w}" height="${disp.h}" preserveAspectRatio="xMidYMid meet"/>`);
        trackX(bannerX, bannerX + disp.w);
        trackY(y, y + disp.h);
        y += disp.h + innerGap;

        const elBaselineY = y + elM.fittedSize * ascentRatioFor(elSegundoText);
        bodyParts.push(`<text x="${slot.centerX}" y="${elBaselineY}" text-anchor="middle" font-family="${FONT_FAMILY_CSS}" font-weight="${elSegundoWeight}" font-size="${elM.fittedSize.toFixed(1)}" fill="rgb(0,0,0)">${escapeXml(elSegundoText)}</text>`);
        trackX(slot.centerX - elM.width / 2, slot.centerX + elM.width / 2);
        trackY(y, y + elM.height);
        y += elM.height + innerGap;

        priceDraw(y);
      }
    }
  } // fin renderSegundoPorFlowHorizontal

  if (doubleDescData) {
    await renderDoubleDescriptionFlowHorizontal();
  } else if (segundoPorData) {
    await renderSegundoPorFlowHorizontal();
  } else if (soloDescripcionData) {
    await renderOnlyDescriptionFlowHorizontal();
  } else {
    await renderNormalFlowHorizontal(soloFraseDescData ? 8 : 4);
  }

  parts.push(`<image href="${template.background_data_url}" xlink:href="${template.background_data_url}" x="0" y="0" width="${cw}" height="${ch}"/>`);
  parts.push(...bodyParts);

  // Recorte al contenido real, con un pequeño margen de aire (mismo criterio
  // que buildSvg).
  const PAD = Math.round(gap * 0.3);
  const crop = {
    left: Math.max(0, Math.floor(contentLeft - PAD)),
    top: Math.max(0, Math.floor(contentTop - PAD)),
    right: Math.min(cw, Math.ceil(contentRight + PAD)),
    bottom: Math.min(ch, Math.ceil(contentBottom + PAD)),
  };
  const cropWidth = crop.right - crop.left;
  const cropHeight = crop.bottom - crop.top;
  const rawRight = Math.ceil(contentRight + PAD);
  const rawBottom = Math.ceil(contentBottom + PAD);

  const svgOpen = `<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="${cropWidth}" height="${cropHeight}" viewBox="${crop.left} ${crop.top} ${cropWidth} ${cropHeight}">`;
  const svgString = [svgOpen, ...defsParts, ...parts, "</svg>"].join("\n");

  return { svgString, crop: { ...crop, width: cropWidth, height: cropHeight, rawRight, rawBottom } };
}


/** Lee el tamaño/proporción real (viewBox o width/height) de un SVG a partir
 *  de su data URL, SIN depender de `img.naturalWidth/naturalHeight` — que
 *  para un SVG que NO trae width/height explícitos en la etiqueta raíz
 *  <svg> (el caso normal de los assets exportados desde Illustrator de este
 *  proyecto: solo traen viewBox) el navegador reporta el tamaño de
 *  reemplazo por defecto de CSS (300x150), sin ninguna relación con la
 *  proporción real del dibujo. Devuelve null si no se pudo determinar (así
 *  el llamador cae de vuelta al método viejo, img.naturalWidth/Height). */
function svgIntrinsicSizeFromDataUrl(dataUrl) {
  try {
    const commaIdx = dataUrl.indexOf(",");
    if (commaIdx === -1) return null;
    const meta = dataUrl.slice(5, commaIdx); // ej. "image/svg+xml;base64"
    const payload = dataUrl.slice(commaIdx + 1);
    const svgText = meta.includes("base64") ? atob(payload) : decodeURIComponent(payload);
    const openTagMatch = svgText.match(/<svg\b[^>]*>/i);
    const openTag = openTagMatch ? openTagMatch[0] : svgText;

    // 1. width/height explícitos en la etiqueta raíz (si vienen en unidades
    //    absolutas, no "%") — el dato más confiable si está presente.
    const wAttr = openTag.match(/\bwidth="([\d.]+)(?:px)?"/i);
    const hAttr = openTag.match(/\bheight="([\d.]+)(?:px)?"/i);
    if (wAttr && hAttr) {
      const w = parseFloat(wAttr[1]), h = parseFloat(hAttr[1]);
      if (w > 0 && h > 0) return { w, h };
    }

    // 2. si no, el viewBox ("minX minY width height") — lo que SÍ traen
    //    siempre estos SVG exportados de Illustrator.
    const vb = openTag.match(/viewBox="[\d.\-]+\s+[\d.\-]+\s+([\d.]+)\s+([\d.]+)"/i);
    if (vb) {
      const w = parseFloat(vb[1]), h = parseFloat(vb[2]);
      if (w > 0 && h > 0) return { w, h };
    }
  } catch (e) {
    // si algo falla al parsear (data URL raro, XML inválido), se cae al
    // método viejo más abajo.
  }
  return null;
}

function loadImageNaturalSize(dataUrl) {
  // Para SVG, preferir SIEMPRE el tamaño intrínseco leído del propio archivo
  // (viewBox/width/height) en vez de img.naturalWidth/naturalHeight del
  // navegador — ver el comentario de svgIntrinsicSizeFromDataUrl(). Esto
  // importa mucho para el cálculo de "contain fit" (containFit) y, con eso,
  // para los límites de recorte (trackX/trackY) del PNG final: una
  // proporción natural equivocada hacía que el recorte final cortara parte
  // de la imagen real aunque el <image> del SVG la mostrara completa — poco
  // notorio con alineación centrada (recortaba un poco de margen invisible
  // de los dos lados), pero muy notorio con alineación a un lado (ej. el
  // callout alineado a la derecha), donde terminaba cortando texto legible.
  if (dataUrl.startsWith("data:image/svg+xml")) {
    const svgSize = svgIntrinsicSizeFromDataUrl(dataUrl);
    if (svgSize) return Promise.resolve(svgSize);
  }
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve({ w: img.naturalWidth, h: img.naturalHeight });
    img.onerror = () => reject(new Error("No se pudo leer el tamaño natural de una imagen"));
    img.src = dataUrl;
  });
}

function containFit(naturalW, naturalH, boxW, boxH) {
  const scale = Math.min(boxW / naturalW, boxH / naturalH);
  return { w: naturalW * scale, h: naturalH * scale };
}

/* =========================================================================
   5. Renderizar SVG -> PNG (canvas) y mostrar preview
   ========================================================================= */

async function renderToPng(svgString, width, height) {
  await document.fonts.ready; // asegura que Work Sans ya esté cargada antes de rasterizar

  const svgBlob = new Blob([svgString], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(svgBlob);
  try {
    const img = new Image();
    const loaded = new Promise((resolve, reject) => {
      img.onload = resolve;
      img.onerror = () => reject(new Error("No se pudo rasterizar el SVG (revisa la consola del navegador)"));
    });
    img.src = url;
    await loaded;

    renderCanvas.width = width;
    renderCanvas.height = height;
    const ctx = renderCanvas.getContext("2d");
    ctx.clearRect(0, 0, width, height);
    ctx.drawImage(img, 0, 0, width, height);

    return await new Promise((resolve, reject) => {
      renderCanvas.toBlob((blob) => {
        if (blob) resolve(blob);
        else reject(new Error("toBlob devolvió null (canvas vacío o corrupto)"));
      }, "image/png");
    });
  } finally {
    URL.revokeObjectURL(url);
  }
}

/* =========================================================================
   6. GENERAR PROMO
   ========================================================================= */

generateBtn.addEventListener("click", async () => {
  generateBtn.disabled = true;
  try {
    if (!state.template) throw new Error("No hay plantilla seleccionada");
    if (!state.excelFields) throw new Error("No hay datos de Excel cargados");

    // Si el Excel trae una columna ORIENTACION (H/V), esa fila decide qué
    // plantilla usar — independiente de la elegida a mano en el desplegable.
    const template = await templateForRow(state.excelFields);
    if (template.id !== state.template.id) {
      log(`Fila con orientación propia: usando "${template.name}" en vez de la plantilla del desplegable.`);
    }

    const mapping = computeMapping(state.excelFields, template.fields);
    const missing = mapping.filter((m) => !m.matched);
    if (missing.length) {
      log(`⚠ Columnas de la plantilla sin datos en el Excel: ${missing.map((m) => m.excel_column).join(", ")}`);
    }

    log("Construyendo SVG...");
    const { svgString, crop } = await buildSvgWithMaxFill(template, mapping, state.excelFields);
    state.lastSvgString = svgString;
    state.lastCrop = crop;       // lo necesita el export a PSD por capas
    state.lastMapping = mapping;

    log(`Recortado al contenido real: ${crop.width}×${crop.height}px (de ${state.template.canvas.width}×${state.template.canvas.height}px original).`);
    log("Mostrando vista previa...");
    svgHost.innerHTML = svgString;
    previewWrap.style.display = "block";

    log("Rasterizando a PNG a resolución completa...");
    const pngBlob = await renderToPng(svgString, crop.width, crop.height);
    state.lastPngBlob = pngBlob;
    log(`PNG generado (${(pngBlob.size / 1024).toFixed(0)} KB).`);

    // Nada de guardar en el servidor: en Still la promo se descarga desde el
    // navegador, que ya tiene los bytes. El /api/save-output del repo original
    // escribía en una carpeta local del servidor y devolvía esa ruta — un
    // supuesto de app de escritorio que no sirve alojado (filesystem efímero,
    // y la ruta no significa nada para quien usa la web).
    log("Listo para descargar (PNG, SVG o PSD por capas).");
  } catch (e) {
    log(`ERROR: ${e.message}`);
    console.error(e);
  } finally {
    generateBtn.disabled = false;
  }
});

function slugifyForFilename(text) {
  return String(text)
    .normalize("NFKD").replace(/[\u0300-\u036f]/g, "")
    .replace(/[^A-Za-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 60) || "fila";
}

function batchLog(msg) {
  const time = new Date().toLocaleTimeString();
  batchLogEl.textContent += `[${time}] ${msg}\n`;
  batchLogEl.scrollTop = batchLogEl.scrollHeight;
}

generateBatchBtn.addEventListener("click", async () => {
  const file = excelInput.files[0];
  if (!file) {
    log("ERROR: selecciona primero un archivo Excel.");
    return;
  }

  generateBatchBtn.disabled = true;
  generateBtn.disabled = true;
  batchPanel.style.display = "block";
  batchLogEl.textContent = "";
  batchTableBody.innerHTML = "";
  batchTable.style.display = "none";
  batchTotal.textContent = "";
  state.batchOutputs = [];

  const totalStart = performance.now();
  try {
    if (!state.template) throw new Error("No hay plantilla seleccionada");

    batchLog("Leyendo todas las filas del Excel...");
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch("/api/promo/read-excel-batch", { method: "POST", body: formData });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Error leyendo el Excel por lotes");
    }
    const json = await res.json();
    const rows = json.rows;
    batchLog(`${rows.length} fila(s) de datos encontradas (hoja "${json.sheet}").`);
    batchTable.style.display = "table";

    // Nombre por fila: usa una columna "Nombre corto" si el Excel la trae
    // (para archivos de salida más legibles); si no, cae a "fila_01", "fila_02", etc.
    function rowDisplayName(fields, rowNumber) {
      const nameField = fields.find((f) => normalize(f.column) === normalize("Nombre corto"));
      const raw = nameField && nameField.value ? nameField.value : `fila_${String(rowNumber).padStart(2, "0")}`;
      return { display: raw, slug: slugifyForFilename(raw) };
    }

    let okCount = 0, errCount = 0;

    for (const row of rows) {
      const { display, slug } = rowDisplayName(row.fields, row.row_number);
      const tr = document.createElement("tr");
      const tdRow = document.createElement("td");
      const tdName = document.createElement("td");
      const tdStatus = document.createElement("td");
      const tdTime = document.createElement("td");
      tdRow.textContent = row.row_number;
      tdName.textContent = display;
      tdStatus.textContent = "Procesando...";
      tdTime.textContent = "";
      tr.append(tdRow, tdName, tdStatus, tdTime);
      batchTableBody.appendChild(tr);

      const rowStart = performance.now();
      try {
        // Cada fila puede pedir su propia orientación (columna ORIENTACION,
        // H/V) — así un mismo lote mezcla filas verticales y horizontales,
        // cada una con la plantilla que le corresponde.
        const template = await templateForRow(row.fields);
        const mapping = computeMapping(row.fields, template.fields);
        const { svgString, crop } = await buildSvgWithMaxFill(template, mapping, row.fields);
        const pngBlob = await renderToPng(svgString, crop.width, crop.height);

        // Vista previa de la PRIMERA fila que sale bien: el lote tarda, y sin
        // esto no se ve nada de lo que se está generando hasta que termina.
        if (!state.batchOutputs.length) {
          svgHost.innerHTML = svgString;
          previewWrap.style.display = "block";
          state.lastSvgString = svgString;
          state.lastCrop = crop;
          state.lastPngBlob = pngBlob;
          batchLog("Vista previa: primera promo del lote.");
        }

        // PSD por fila, solo si se pidió: son ~6 rasterizaciones extra por
        // promo, así que multiplica bastante el tiempo del lote.
        let psdBlob = null;
        if (batchPsdChk && batchPsdChk.checked) {
          const layers = await rasterizeLayers(svgString, crop.width, crop.height);
          if (layers.length) {
            const form = new FormData();
            layers.forEach((l, i) => form.append("layers", l.blob, `layer_${i}.png`));
            form.append("names", JSON.stringify(layers.map((l) => l.name)));
            form.append("filename", `promo_${slug}`);
            const psdRes = await fetch("/api/promo/psd", { method: "POST", body: form });
            if (!psdRes.ok) {
              const e = await psdRes.json().catch(() => ({}));
              throw new Error(e.detail || `PSD: error ${psdRes.status}`);
            }
            psdBlob = await psdRes.blob();
          }
        }

        // No se guarda en el servidor (ver nota en el generador individual):
        // se acumula en memoria y al final todo el lote baja en un solo ZIP,
        // mismo patrón que ya usa "Por lotes" de bodegones.
        const outName = `promo_${slug}`;
        state.batchOutputs.push({
          name: outName,
          png: pngBlob,
          svg: new Blob([svgString], { type: "image/svg+xml" }),
          psd: psdBlob,
        });

        const elapsed = performance.now() - rowStart;
        tdStatus.textContent = "✓ OK";
        tdStatus.className = "status ok";
        tdTime.textContent = `${(elapsed / 1000).toFixed(2)} s`;
        okCount++;
        batchLog(`Fila ${row.row_number} (${display}): OK en ${(elapsed / 1000).toFixed(2)}s`);
      } catch (e) {
        const elapsed = performance.now() - rowStart;
        tdStatus.textContent = `✗ Error: ${e.message}`;
        tdStatus.className = "status err";
        tdTime.textContent = `${(elapsed / 1000).toFixed(2)} s`;
        errCount++;
        batchLog(`Fila ${row.row_number} (${display}): ERROR en ${(elapsed / 1000).toFixed(2)}s — ${e.message}`);
        console.error(`Fila ${row.row_number}`, e);
      }
    }

    const totalElapsed = performance.now() - totalStart;
    const avg = rows.length ? totalElapsed / rows.length : 0;
    batchTotal.textContent =
      `Total: ${rows.length} fila(s) — ${okCount} OK, ${errCount} con error. ` +
      `Tiempo total: ${(totalElapsed / 1000).toFixed(2)} s (promedio ${(avg / 1000).toFixed(2)} s/fila).`;
    batchLog(`--- Lote terminado: ${(totalElapsed / 1000).toFixed(2)}s total, promedio ${(avg / 1000).toFixed(2)}s/fila ---`);
  } catch (e) {
    batchLog(`ERROR: ${e.message}`);
    console.error(e);
  } finally {
    generateBatchBtn.disabled = false;
    generateBtn.disabled = false;
  }
});

downloadPngBtn.addEventListener("click", () => {
  if (!state.lastPngBlob) return;
  const a = document.createElement("a");
  a.href = URL.createObjectURL(state.lastPngBlob);
  a.download = "promo_generada.png";
  a.click();
});

/* El SVG se arma con medidas FIJAS en píxeles porque es lo que necesita el
   rasterizado a PNG. Pero al abrir ese archivo suelto el navegador no lo
   escala: monta un documento de miles de píxeles de ancho y solo se ve la
   esquina superior izquierda, casi siempre transparente — parece vacío o
   roto, aunque el contenido esté perfecto (reporte real del usuario).
   Para el archivo descargado se pasan a 100%: el viewBox ya define la
   proporción, así que el dibujo no cambia, solo se adapta a lo que lo abra. */
function svgParaDescargar(svgString) {
  return svgString.replace(
    /^(\s*<svg\b[^>]*?)\swidth="[^"]*"\s+height="[^"]*"/,
    '$1 width="100%" height="100%"'
  );
}

downloadSvgBtn.addEventListener("click", () => {
  if (!state.lastSvgString) return;
  const blob = new Blob([svgParaDescargar(state.lastSvgString)], { type: "image/svg+xml" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "promo_generada.svg";
  a.click();
});

/* =========================================================================
   PSD por capas

   El PSD se arma con UNA CAPA POR CAMPO de la plantilla (fondo, PESOS,
   CALLOUT, DESCRIPCION, ...), para poder ajustar cada uno por separado en
   Photoshop — mismo criterio que el PSD de bodegones (una capa por producto).

   El truco: cada capa se rasteriza ACÁ, en el navegador, escondiendo todos
   los demás campos del SVG. Así el PSD coincide exactamente con el preview,
   porque lo dibuja el mismo motor. Si el servidor volviera a dibujar el texto
   con Pillow, la negrilla/tachado/superíndice por palabra no coincidirían.
   ========================================================================= */
/* Nombre de capa legible en Photoshop. El SVG no agrupa por campo (cada
   texto es un nodo suelto), así que se nombra por su contenido real, que es
   lo que el diseñador reconoce al abrir el archivo: "Texto: $99" dice más
   que "Capa 3". */
function layerNameFor(node, index) {
  const explicit = node.getAttribute("data-field") || node.getAttribute("id");
  if (explicit) return explicit;
  const tag = node.tagName.toLowerCase();
  if (tag === "image") return index === 0 ? "Fondo" : "Imagen";
  const text = (node.textContent || "").replace(/\s+/g, " ").trim();
  if (text) return `Texto: ${text.slice(0, 40)}`;
  return `Capa ${index}`;
}

async function rasterizeLayers(svgString, width, height) {
  const doc = new DOMParser().parseFromString(svgString, "image/svg+xml");
  const root = doc.documentElement;
  // Cada hijo directo del <svg> es un campo (o el fondo). Se rasteriza uno
  // por vez ocultando el resto con display:none.
  const nodes = Array.from(root.children).filter(
    (n) => n.tagName !== "defs" && n.tagName !== "style"
  );
  if (!nodes.length) return [];

  const out = [];
  for (let i = 0; i < nodes.length; i++) {
    nodes.forEach((n, j) => n.setAttribute("style", j === i ? "" : "display:none"));
    const one = new XMLSerializer().serializeToString(root);
    const blob = await renderToPng(one, width, height);
    out.push({ name: layerNameFor(nodes[i], i), blob });
  }
  nodes.forEach((n) => n.removeAttribute("style"));
  return out;
}

const downloadPsdBtn = document.getElementById("downloadPsdBtn");
if (downloadPsdBtn) {
  downloadPsdBtn.addEventListener("click", async () => {
    if (!state.lastSvgString || !state.lastCrop) return;
    downloadPsdBtn.disabled = true;
    const original = downloadPsdBtn.textContent;
    downloadPsdBtn.textContent = "Armando PSD...";
    try {
      log("Rasterizando una capa por campo...");
      const layers = await rasterizeLayers(
        state.lastSvgString, state.lastCrop.width, state.lastCrop.height
      );
      if (!layers.length) throw new Error("No se pudo separar la plantilla en capas");

      const form = new FormData();
      layers.forEach((l, i) => form.append("layers", l.blob, `layer_${i}.png`));
      form.append("names", JSON.stringify(layers.map((l) => l.name)));
      form.append("filename", "promo_generada");

      log(`Enviando ${layers.length} capas al servidor...`);
      const res = await fetch("/api/promo/psd", { method: "POST", body: form });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Error ${res.status}`);
      }
      const blob = await res.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "promo_generada.psd";
      a.click();
      log(`PSD descargado (${layers.length} capas, ${(blob.size / 1024).toFixed(0)} KB).`);
    } catch (e) {
      log(`ERROR al armar el PSD: ${e.message}`);
      console.error(e);
    } finally {
      downloadPsdBtn.disabled = false;
      downloadPsdBtn.textContent = original;
    }
  });
}

/* ── Descarga del lote completo en un ZIP ─────────────────────────────── */
const downloadZipBtn = document.getElementById("downloadZipBtn");
if (downloadZipBtn) {
  downloadZipBtn.addEventListener("click", async () => {
    if (!state.batchOutputs.length) return;
    downloadZipBtn.disabled = true;
    const original = downloadZipBtn.textContent;
    downloadZipBtn.textContent = "Comprimiendo...";
    try {
      const form = new FormData();
      const names = [];
      for (const o of state.batchOutputs) {
        form.append("files", o.png, `${o.name}.png`);
        names.push(`${o.name}.png`);
        form.append("files", o.svg, `${o.name}.svg`);
        names.push(`${o.name}.svg`);
        if (o.psd) {
          form.append("files", o.psd, `${o.name}.psd`);
          names.push(`${o.name}.psd`);
        }
      }
      form.append("names", JSON.stringify(names));
      const res = await fetch("/api/promo/zip", { method: "POST", body: form });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Error ${res.status}`);
      }
      const blob = await res.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "promos.zip";
      a.click();
      batchLog(`ZIP descargado (${state.batchOutputs.length} promos).`);
    } catch (e) {
      batchLog(`ERROR al armar el ZIP: ${e.message}`);
      console.error(e);
    } finally {
      downloadZipBtn.disabled = false;
      downloadZipBtn.textContent = original;
    }
  });
}

/* =========================================================================
   Init

   Perezoso a propósito: en Still las plantillas viven detrás de la sesión
   (/api/promo/* está en PROTECTED_PREFIXES), así que pedirlas al cargar la
   página daría 401 mientras el usuario todavía no inició sesión. switchTab()
   llama a promoInit() la primera vez que se abre la pestaña.
   ========================================================================= */
/* ── Biblioteca de imágenes ───────────────────────────────────────────────
   La administración de la biblioteca (subir, borrar, tipos, colores y
   tipografía) pasó a promo_kits/promo_lab.js, porque dejó de ser una lista
   plana de archivos y se volvió un "kit de marca". Acá solo queda el enganche
   para que se inicialice al abrir la pestaña. */

let PROMO_READY = false;
window.promoInit = function () {
  if (PROMO_READY) return;
  PROMO_READY = true;
  loadTemplates();
  if (window.promoKitInit) window.promoKitInit();
};
