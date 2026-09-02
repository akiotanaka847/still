/* Motor declarativo de mecánicas promocionales.
 * ────────────────────────────────────────────────────────────────────────────
 * Hoy cada variante de promo está escrita a mano en promo.js: ~1266 líneas
 * repartidas en 8 funciones (4 variantes × 2 orientaciones). Agregar una
 * mecánica nueva es escribir otro par de funciones, y el propio promo.js ya
 * anota que la generalización pendiente es declarar el orden en vez de
 * codificarlo.
 *
 * Este archivo hace eso: interpreta una mecánica DECLARADA como una lista de
 * bloques. La guía de mecánicas de OXXO ya las define exactamente así —
 * "Descripción de artículo + a sólo + precio + c/u"— o sea que la declaración
 * es una transcripción del documento, no una interpretación nuestra.
 *
 * Convive con los flujos escritos a mano en vez de reemplazarlos: mientras no
 * esté probado que produce EXACTAMENTE lo mismo, el original manda. Se activa
 * con window.PROMO_FLOW_DECLARADO.
 *
 * Reusa los helpers globales de promo.js (fitParagraph, lineToSvgText,
 * renderPesosBlock, …) igual que hace promo_lab.js: no se reimplementa nada de
 * la maquinaria de medición y ajuste, que es la parte delicada y ya probada.
 */

/* ── Vocabulario de bloques ───────────────────────────────────────────────
 * Cada mecánica es una lista de estos seis. Salieron de leer los flujos que
 * ya existen: son los únicos tipos de cosa que una promo apila.
 *
 *   asset      imagen que NOMBRA el dato        (CALLOUT, BRAND, PLECA)
 *   line       una línea de texto ajustada      (FRASE APOYO)
 *   paragraph  párrafo de N líneas como máximo  (DESCRIPCION)
 *   price      bloque de precio o porcentaje    (PESOS)
 *   literal    texto FIJO de la mecánica        ("COMPRA", "GRATIS", "a sólo")
 *   banner     imagen fija del motor            (LLÉVATE, Y LLÉVATE)
 *
 * Un bloque cuyo campo no viene en el dato simplemente no se dibuja, y lo que
 * sigue sube: es el mismo comportamiento que ya tienen los flujos a mano.
 */

const BLOQUES = {
  /* Imagen que nombra el dato. Se dibuja dentro de la caja de la plantilla,
   * en modo "contain" (cabe entera, centrada, sin deformar). */
  async asset(paso, ctx) {
    const f = ctx.fieldsByColumn[paso.field];
    if (!ctx.isPresent(f)) return;
    ctx.advance();
    const dataUrl = await fetchAssetDataUrl(f.value);
    const natural = await loadImageNaturalSize(dataUrl);
    const disp = containFit(natural.w, natural.h, f.box.width, f.box.height);
    const dispLeft = f.box.x + (f.box.width - disp.w) / 2;
    ctx.bodyParts.push(`<image href="${dataUrl}" xlink:href="${dataUrl}" x="${f.box.x}" y="${ctx.cursorY}" width="${f.box.width}" height="${f.box.height}" preserveAspectRatio="xMidYMid meet"/>`);
    ctx.trackX(dispLeft, dispLeft + disp.w);
    ctx.trackY(ctx.cursorY, ctx.cursorY + f.box.height);
    ctx.cursorY += f.box.height;
  },

  /* Una sola línea, centrada, encogida hasta caber en el 90% del ancho. */
  async line(paso, ctx) {
    const f = ctx.fieldsByColumn[paso.field];
    if (!ctx.isPresent(f)) return;
    ctx.advance();
    const weight = weightForFontName(f.font_family);
    const runs = f.runs && f.runs.length ? f.runs : [{ text: f.value }];
    const plainText = runs.map((r) => r.text).join("");
    const fittedSize = fitFontSizeToWidth(plainText, weight, f.font_size, ctx.cw * 0.9);
    const lineWidth = measureTextWidth(plainText, weight, fittedSize);
    const baselineY = ctx.cursorY + fittedSize * ascentRatioFor(plainText);
    ctx.bodyParts.push(lineToSvgText(runsToWords(runs), weight, fittedSize, f.color, ctx.cw / 2, baselineY));
    const lineHeight = paragraphHeight(1, fittedSize, plainText);
    ctx.trackX((ctx.cw - lineWidth) / 2, (ctx.cw + lineWidth) / 2);
    ctx.trackY(ctx.cursorY, ctx.cursorY + lineHeight);
    ctx.cursorY += lineHeight;
  },

  /* Párrafo de varias líneas. Dos modos:
   *   - normal: hasta maxLines, con el alto de la caja como tope (escalado si
   *     maxLines > 4, que es como la plantilla dimensionó la caja).
   *   - fit "canvas": usa el alto ENTERO del lienzo. Es la variante
   *     "comunicación de imagen", donde la descripción es lo único que hay. */
  async paragraph(paso, ctx) {
    const f = ctx.fieldsByColumn[paso.field];
    if (!ctx.isPresent(f)) return;
    const maxLines = paso.maxLines || 4;
    const weight = weightForFontName(f.font_family);
    const runs = f.runs && f.runs.length ? f.runs : [{ text: f.value }];
    const alCanvas = paso.fit === "canvas";

    const maxHeight = alCanvas ? ctx.ch - 2 * ctx.gap
                               : f.box.height * (maxLines / 4);
    if (!alCanvas) ctx.advance();

    const { fontSize: fittedSize, lines, actualHeight, maxLineWidth } =
      fitParagraph(runs, weight, f.font_size, f.box.width, maxHeight, maxLines);

    const plainText = lines.map((l) => l.map((w) => w.text).join(" ")).join(" ");
    const firstLineAscent = ascentRatioFor(plainText);
    if (alCanvas) ctx.cursorY = ctx.gap;
    const arranque = ctx.cursorY;
    lines.forEach((line, i) => {
      const baselineY = arranque + fittedSize * firstLineAscent + i * fittedSize * LINE_HEIGHT_RATIO;
      ctx.bodyParts.push(lineToSvgText(line, weight, fittedSize, f.color, ctx.cw / 2, baselineY));
    });
    ctx.trackX((ctx.cw - maxLineWidth) / 2, (ctx.cw + maxLineWidth) / 2);
    ctx.trackY(arranque, arranque + actualHeight);
    ctx.cursorY = arranque + actualHeight;
  },

  /* Precio o porcentaje. La composición interna ($ chico + número grande, o
   * "NN % desc") vive en renderPesosBlock y no se toca desde acá. */
  async price(paso, ctx) {
    const f = ctx.fieldsByColumn[paso.field];
    if (!ctx.isPresent(f)) return;
    // El bloque de precio usa una fuente enorme y su fórmula de alto reserva
    // mucho ascenso, lo que hacía ver el espacio previo más grande de lo real;
    // por eso la guía de espaciado de este paso viene declarada aparte.
    ctx.advance(paso.gap != null ? paso.gap : 1);
    const weight = weightForFontName(f.font_family);
    const prefixWeight = f.prefix ? weightForFontName(f.prefix.font_family) : weight;
    ctx.cursorY = renderPesosBlock(f, weight, prefixWeight, ctx.cw / 2, ctx.cw * 0.9,
                                   ctx.cursorY, ctx.bodyParts, ctx.trackX, ctx.trackY);
  },

  /* Texto FIJO de la mecánica ("COMPRA", "GRATIS", "a sólo", "c/u"). No viene
   * del dato: lo pone la mecánica. Toma tipografía y tamaño de otro campo
   * (`style_from`) para que respete la plantilla sin declarar medidas acá. */
  async literal(paso, ctx) {
    const texto = paso.text;
    if (!texto) return;
    const molde = ctx.fieldsByColumn[paso.style_from] || ctx.fieldsByColumn["FRASE APOYO"];
    const fuente = paso.font_family || (molde && molde.font_family) || "WorkSans-Black";
    const tamBase = paso.font_size || (molde && molde.font_size) || 727;
    const color = paso.color || (molde && molde.color) || "rgb(0,0,0)";
    const weight = weightForFontName(fuente);

    ctx.advance(paso.gap != null ? paso.gap : 1);
    const fittedSize = fitFontSizeToWidth(texto, weight, tamBase, ctx.cw * 0.9);
    const ancho = measureTextWidth(texto, weight, fittedSize);
    const baselineY = ctx.cursorY + fittedSize * ascentRatioFor(texto);
    ctx.bodyParts.push(
      `<text x="${ctx.cw / 2}" y="${baselineY}" text-anchor="middle" font-family="${FONT_FAMILY_CSS}" font-weight="${weight}" font-size="${fittedSize.toFixed(1)}" fill="${color}">${escapeXml(texto)}</text>`);
    const alto = paragraphHeight(1, fittedSize, texto);
    ctx.trackX((ctx.cw - ancho) / 2, (ctx.cw + ancho) / 2);
    ctx.trackY(ctx.cursorY, ctx.cursorY + alto);
    ctx.cursorY += alto;
  },

  /* Banner fijo del motor (LLÉVATE, Y LLÉVATE). El nombre de archivo NO se
   * escribe acá: se resuelve por clave contra la plantilla o el kit, que es lo
   * que permite cambiarlo sin tocar código (ver systemAsset en promo.js). */
  async banner(paso, ctx) {
    const archivo = systemAsset(paso.key);
    if (!archivo) return;
    const anchoCaja = paso.width_ratio ? ctx.cw * paso.width_ratio : ctx.cw * 0.9;
    const dataUrl = await fetchAssetDataUrl(archivo);
    const natural = await loadImageNaturalSize(dataUrl);
    const alto = anchoCaja * (natural.h / natural.w);
    ctx.advance(paso.gap != null ? paso.gap : 1);
    const x = (ctx.cw - anchoCaja) / 2;
    ctx.bodyParts.push(`<image href="${dataUrl}" xlink:href="${dataUrl}" x="${x.toFixed(1)}" y="${ctx.cursorY.toFixed(1)}" width="${anchoCaja.toFixed(1)}" height="${alto.toFixed(1)}" preserveAspectRatio="xMidYMid meet"/>`);
    ctx.trackX(x, x + anchoCaja);
    ctx.trackY(ctx.cursorY, ctx.cursorY + alto);
    ctx.cursorY += alto;
  },
};

/* ── Mecánicas declaradas ─────────────────────────────────────────────────
 * Transcritas de la guía "Tipos de Mecánicas promocionales" de OXXO.
 * Por ahora solo las que ya sabe dibujar el motor, para poder verificar que la
 * versión declarada da EXACTAMENTE lo mismo que la escrita a mano. Recién
 * cuando eso esté probado tiene sentido declarar las que faltan.
 */
const MECANICAS = {
  /* Guía: "Descripción de artículo + a sólo + precio + c/u", con hasta dos
   * textos de apoyo (uno antes, uno después). Cubre Simple y Múltiple, que
   * comparten estructura y solo difieren en cómo se compone el precio. */
  simple: {
    nombre: "Simple / Múltiple",
    flow: [
      { kind: "asset", field: "CALLOUT" },
      { kind: "line", field: "FRASE APOYO" },
      { kind: "price", field: "PESOS", gap: 0.75 },
      { kind: "paragraph", field: "DESCRIPCION", maxLines: 4 },
      { kind: "asset", field: "BRAND" },
    ],
  },

  /* Misma estructura, pero la descripción puede ocupar hasta 8 líneas: es el
   * caso "solo frase de apoyo + descripción", sin precio que compita. */
  simple_descripcion_larga: {
    nombre: "Simple con descripción larga",
    flow: [
      { kind: "asset", field: "CALLOUT" },
      { kind: "line", field: "FRASE APOYO" },
      { kind: "price", field: "PESOS", gap: 0.75 },
      { kind: "paragraph", field: "DESCRIPCION", maxLines: 8 },
      { kind: "asset", field: "BRAND" },
    ],
  },

  /* Guía, "Comunicación de imagen": solo la descripción. Sin callout, sin
   * marca y sin frase, aunque el dato los traiga. Usa el alto completo. */
  comunicacion_imagen: {
    nombre: "Comunicación de imagen",
    flow: [
      { kind: "paragraph", field: "DESCRIPCION", maxLines: 20, fit: "canvas" },
    ],
  },
};

/** Dibuja una mecánica declarada. `ctx` lo arma el motor (ver ctxFlujo en
 *  promo.js) y trae el estado del armado: cursor, lienzo, medidas y dónde
 *  acumular lo dibujado. */
async function renderMecanicaDeclarada(id, ctx) {
  const mec = MECANICAS[id];
  if (!mec) throw new Error(`Mecánica no declarada: ${id}`);
  for (const paso of mec.flow) {
    const bloque = BLOQUES[paso.kind];
    if (!bloque) throw new Error(`Tipo de bloque desconocido: ${paso.kind}`);
    await bloque(paso, ctx);
  }
}

window.MECANICAS = MECANICAS;
