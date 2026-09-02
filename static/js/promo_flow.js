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

/** Resuelve el campo de un paso.
 *
 *  Lo normal es que exista en la plantilla (CALLOUT, PESOS, DESCRIPCION…). Pero
 *  algunas mecánicas usan columnas que la plantilla NO declara — "DESCRIPCION 2"
 *  del promopack, por ejemplo, que no tiene capa propia en el PSD. Esas se leen
 *  del dato crudo y heredan caja y tipografía de un campo molde, que es lo
 *  mismo que ya hacen a mano los flujos de doble descripción. */
function campoDelPaso(paso, ctx) {
  const directo = ctx.fieldsByColumn[paso.field];
  if (directo) return directo;
  const crudo = ctx.rawFieldByColumn(paso.field);
  const molde = ctx.fieldsByColumn[paso.style_from || "DESCRIPCION"];
  if (!crudo || !molde) return null;
  return { ...molde, value: crudo.value, runs: crudo.runs, matched: true };
}

/** ¿Esta promo es de un producto con alcohol? Lo marca el backend leyendo la
 *  categoría del dato (ver looks_like_alcohol en promo_excel.py). */
function esAlcohol(ctx) {
  return !!ctx.rawFieldByColumn("PROMO_ALCOHOL");
}

/** La guía prohíbe "Compra", "Llévate" y "Gratis" en promociones con alcohol:
 *  no se puede redactar incentivando la compra. Un paso marcado así se omite
 *  cuando la promo es de alcohol, y queda dicho en el log — callarlo sería
 *  peor, porque quien revisa la pieza no vería por qué le falta algo. */
function pasoProhibido(paso, ctx) {
  if (!paso.prohibido_con_alcohol || !esAlcohol(ctx)) return false;
  log(`⚠ "${paso.text || paso.key}" no se dibuja: la guía lo prohíbe en promociones con alcohol.`);
  return true;
}

const BLOQUES = {
  /* Imagen que nombra el dato. Se dibuja dentro de la caja de la plantilla,
   * en modo "contain" (cabe entera, centrada, sin deformar). */
  async asset(paso, ctx) {
    const f = campoDelPaso(paso, ctx);
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
    const f = campoDelPaso(paso, ctx);
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
    const f = campoDelPaso(paso, ctx);
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
    const f = campoDelPaso(paso, ctx);
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
    if (!texto || pasoProhibido(paso, ctx)) return;
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
    if (pasoProhibido(paso, ctx)) return;
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

  /* ── Mecánicas de la guía que el motor todavía no sabía dibujar ──────────
   * Se declaran, no se programan: son las mismas seis piezas en otro orden.
   * Ninguna se elige sola todavía — hay que pedirlas por TIPO_MECANICA o a
   * mano — porque decidir cuándo aplican es un problema del dato, no del
   * dibujo, y quería que se pudieran ver antes de atarlas a una regla. */

  /* Guía, "Comunicación de precio regular":
   *   Descripción de artículo + a sólo + precio + c/u
   * "A SÓLO" y "c/u" no vienen del dato: los pone la mecánica. */
  comunicacion_precio_regular: {
    nombre: "Comunicación de precio regular",
    flow: [
      { kind: "literal", text: "A SÓLO", style_from: "FRASE APOYO" },
      { kind: "price", field: "PESOS", gap: 0.75 },
      { kind: "literal", text: "c/u", style_from: "FRASE APOYO", gap: 0.4 },
      { kind: "paragraph", field: "DESCRIPCION", maxLines: 4 },
      { kind: "asset", field: "BRAND" },
    ],
  },

  /* Guía, "Promopack (suma de artículos por precio)":
   *   Descripción de artículo 1 + "+" + Descripción de artículo 2 + Precio
   * DESCRIPCION 2 no tiene capa en el PSD: hereda caja y tipografía de
   * DESCRIPCION (ver campoDelPaso). */
  promopack_suma: {
    nombre: "Promopack — suma de artículos por precio",
    flow: [
      { kind: "asset", field: "CALLOUT" },
      { kind: "paragraph", field: "DESCRIPCION", maxLines: 3 },
      { kind: "literal", text: "+", style_from: "PESOS", gap: 0.5 },
      { kind: "paragraph", field: "DESCRIPCION 2", style_from: "DESCRIPCION", maxLines: 3 },
      { kind: "price", field: "PESOS", gap: 0.75 },
      { kind: "asset", field: "BRAND" },
    ],
  },

  /* Guía, "Promopack (Compra artículo = gratis)":
   *   Texto de apoyo inicial + Descripción + "Llévate Gratis" + Descripción 2
   * Las tres palabras marcadas caen bajo la prohibición de alcohol: en una
   * promo de cerveza se omiten y queda avisado en el log. */
  promopack_gratis: {
    nombre: "Promopack — compra y llévate gratis",
    flow: [
      { kind: "asset", field: "CALLOUT" },
      { kind: "line", field: "FRASE APOYO" },
      { kind: "literal", text: "COMPRA", style_from: "FRASE APOYO", prohibido_con_alcohol: true },
      { kind: "paragraph", field: "DESCRIPCION", maxLines: 3 },
      { kind: "banner", key: "banner_llevate", width_ratio: 0.6, prohibido_con_alcohol: true },
      { kind: "literal", text: "GRATIS", style_from: "PESOS", prohibido_con_alcohol: true },
      { kind: "paragraph", field: "DESCRIPCION 2", style_from: "DESCRIPCION", maxLines: 3 },
      { kind: "asset", field: "BRAND" },
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

/* ── Horizontal: columnas, no pila ────────────────────────────────────────
 * La vertical apila: el cursor solo baja, así que medir y dibujar es un solo
 * paso. La horizontal reparte en COLUMNAS y centra cada una verticalmente en
 * el lienzo, así que hay que saber el alto del grupo ANTES de dibujarlo. Por
 * eso acá cada bloque se parte en medir() y dibujar(): no es un capricho de
 * diseño, es lo que impone centrar.
 *
 * Los topes de ancho por columna (maxCalloutWidth y compañía) los calcula
 * promo.js a partir de las cajas ORIGINALES de la plantilla y llegan en el
 * ctx: dependen del PSD, no de la mecánica, así que no se declaran acá.
 */
const BLOQUES_COLUMNA = {
  asset: {
    async medir(paso, ctx) {
      const f = campoDelPaso(paso, ctx);
      if (!ctx.isPresent(f)) return null;
      const tope = ctx.topes[paso.cap];
      // El callout se alinea a la DERECHA contra un borde fijo; el resto se
      // centra sobre su propio centro. Ver el porqué en promo.js: con el
      // callout centrado, uno angosto dejaba un hueco distinto fila a fila.
      const derecha = paso.align === "right";
      const ancho = tope != null ? Math.min(f.box.width, tope) : f.box.width;
      const par = derecha ? "xMaxYMid meet" : "xMidYMid meet";
      const dataUrl = await fetchAssetDataUrl(f.value, par);
      const natural = await loadImageNaturalSize(dataUrl);
      return { f, ancho, alto: f.box.height, dataUrl, natural, derecha, par };
    },
    dibujar(paso, ctx, m, y) {
      const { f, ancho, dataUrl, natural, derecha, par } = m;
      const x = derecha
        ? ctx.topes.calloutRightEdge - ancho
        : f.box.x + f.box.width / 2 - ancho / 2;
      const disp = containFit(natural.w, natural.h, ancho, f.box.height);
      const dispLeft = derecha ? x + ancho - disp.w : x + (ancho - disp.w) / 2;
      ctx.bodyParts.push(`<image href="${dataUrl}" xlink:href="${dataUrl}" x="${x}" y="${y}" width="${ancho}" height="${f.box.height}" preserveAspectRatio="${par}"/>`);
      ctx.trackX(dispLeft, dispLeft + disp.w);
      ctx.trackY(y, y + f.box.height);
    },
  },

  line: {
    async medir(paso, ctx) {
      const f = campoDelPaso(paso, ctx);
      if (!ctx.isPresent(f)) return null;
      const weight = weightForFontName(f.font_family);
      const runs = f.runs && f.runs.length ? f.runs : [{ text: f.value }];
      const plainText = runs.map((r) => r.text).join("");
      const maxWidth = Math.min(f.box.width, ctx.topes[paso.cap]) * 0.95;
      const fittedSize = fitFontSizeToWidth(plainText, weight, f.font_size, maxWidth);
      return {
        f, weight, runs, plainText, fittedSize,
        ancho: measureTextWidth(plainText, weight, fittedSize),
        alto: paragraphHeight(1, fittedSize, plainText),
        centerX: f.box.x + f.box.width / 2,
      };
    },
    dibujar(paso, ctx, m, y) {
      const baselineY = y + m.fittedSize * ascentRatioFor(m.plainText);
      ctx.bodyParts.push(lineToSvgText(runsToWords(m.runs), m.weight, m.fittedSize, m.f.color, m.centerX, baselineY));
      ctx.trackX(m.centerX - m.ancho / 2, m.centerX + m.ancho / 2);
      ctx.trackY(y, y + m.alto);
    },
  },

  paragraph: {
    async medir(paso, ctx) {
      const f = campoDelPaso(paso, ctx);
      if (!ctx.isPresent(f)) return null;
      const maxLines = paso.maxLines || 4;
      const weight = weightForFontName(f.font_family);
      const runs = f.runs && f.runs.length ? f.runs : [{ text: f.value }];
      // El techo por líneas nunca puede pasarse de lo que REALMENTE cabe en el
      // lienzo: el horizontal es más bajo que el vertical (6466 vs 8000), y con
      // 8 líneas el doble de alto empujaba a BRAND fuera del lienzo.
      const reservaAbajo = paso.reserve_for && ctx.fieldsByColumn[paso.reserve_for] &&
                           ctx.isPresent(ctx.fieldsByColumn[paso.reserve_for])
        ? ctx.gap + ctx.fieldsByColumn[paso.reserve_for].box.height : 0;
      const disponible = Math.max(f.box.height * 0.4, ctx.ch * 0.98 - reservaAbajo);
      const maxHeight = Math.min(f.box.height * (maxLines / 4) * 0.97, disponible);
      const tope = ctx.topes[paso.cap];
      const wrapWidth = tope != null ? Math.min(f.box.width, tope) : f.box.width;
      const r = fitParagraph(runs, weight, f.font_size, wrapWidth, maxHeight, maxLines);
      return {
        f, weight, ...r, alto: r.actualHeight, ancho: r.maxLineWidth,
        plainText: r.lines.map((l) => l.map((w) => w.text).join(" ")).join(" "),
        centerX: f.box.x + f.box.width / 2,
      };
    },
    dibujar(paso, ctx, m, y) {
      const asc = ascentRatioFor(m.plainText);
      m.lines.forEach((line, i) => {
        const baselineY = y + m.fontSize * asc + i * m.fontSize * LINE_HEIGHT_RATIO;
        ctx.bodyParts.push(lineToSvgText(line, m.weight, m.fontSize, m.f.color, m.centerX, baselineY));
      });
      ctx.trackX(m.centerX - m.ancho / 2, m.centerX + m.ancho / 2);
      ctx.trackY(y, y + m.alto);
    },
  },

  price: {
    async medir(paso, ctx) {
      const f = campoDelPaso(paso, ctx);
      if (!ctx.isPresent(f)) return null;
      const weight = weightForFontName(f.font_family);
      const prefixWeight = f.prefix ? weightForFontName(f.prefix.font_family) : weight;
      const centerX = f.box.x + f.box.width / 2;
      const maxWidth = Math.min(f.box.width, ctx.topes[paso.cap]) * 0.95;
      // renderPesosBlock SIEMPRE dibuja: se lo llama una vez al vacío, contra
      // un bodyParts descartable y desde y=0, solo para conocer su alto real
      // (el cursor que devuelve, habiendo arrancado en 0, ES el alto).
      const alto = renderPesosBlock(f, weight, prefixWeight, centerX, maxWidth, 0, [], () => {}, () => {});
      return { f, weight, prefixWeight, centerX, maxWidth, alto, ancho: maxWidth };
    },
    dibujar(paso, ctx, m, y) {
      renderPesosBlock(m.f, m.weight, m.prefixWeight, m.centerX, m.maxWidth, y,
                       ctx.bodyParts, ctx.trackX, ctx.trackY);
    },
  },
};

/* Mecánicas en horizontal. Mismos bloques, agrupados en columnas. */
const MECANICAS_HORIZONTAL = {
  simple: {
    nombre: "Simple / Múltiple (horizontal)",
    columnas: [
      { flow: [{ kind: "asset", field: "CALLOUT", align: "right", cap: "callout" }] },
      { flow: [
        { kind: "paragraph", field: "DESCRIPCION", maxLines: 4, cap: "descripcion", reserve_for: "BRAND" },
        { kind: "asset", field: "BRAND", cap: "descripcion" },
      ] },
      { flow: [
        { kind: "line", field: "FRASE APOYO", cap: "frase" },
        // El espacio antes del precio va reducido: su fórmula de alto reserva
        // mucho ascenso por la fuente enorme y con el gap completo se veía
        // más separado que el resto de las transiciones.
        { kind: "price", field: "PESOS", cap: "pesos", gap: 0.4875 },
      ] },
    ],
  },
  simple_descripcion_larga: {
    nombre: "Simple con descripción larga (horizontal)",
    columnas: [
      { flow: [{ kind: "asset", field: "CALLOUT", align: "right", cap: "callout" }] },
      { flow: [
        { kind: "paragraph", field: "DESCRIPCION", maxLines: 8, cap: "descripcion", reserve_for: "BRAND" },
        { kind: "asset", field: "BRAND", cap: "descripcion" },
      ] },
      { flow: [
        { kind: "line", field: "FRASE APOYO", cap: "frase" },
        { kind: "price", field: "PESOS", cap: "pesos", gap: 0.4875 },
      ] },
    ],
  },
};

/** Dibuja una mecánica horizontal: mide cada columna entera, la centra en el
 *  alto del lienzo y recién ahí dibuja. */
async function renderMecanicaHorizontal(id, ctx) {
  const mec = MECANICAS_HORIZONTAL[id];
  if (!mec) throw new Error(`Mecánica horizontal no declarada: ${id}`);

  for (const columna of mec.columnas) {
    const medidas = [];
    for (const paso of columna.flow) {
      const bloque = BLOQUES_COLUMNA[paso.kind];
      if (!bloque) throw new Error(`Tipo de bloque desconocido: ${paso.kind}`);
      medidas.push({ paso, m: await bloque.medir(paso, ctx) });
    }
    const presentes = medidas.filter((x) => x.m);
    if (!presentes.length) continue;

    // Alto del grupo = suma de altos + los espacios ENTRE los que sí están.
    let total = 0;
    presentes.forEach((x, i) => {
      if (i > 0) total += ctx.gap * (x.paso.gap != null ? x.paso.gap : 1);
      total += x.m.alto;
    });

    let y = (ctx.ch - total) / 2;
    presentes.forEach((x, i) => {
      if (i > 0) y += ctx.gap * (x.paso.gap != null ? x.paso.gap : 1);
      BLOQUES_COLUMNA[x.paso.kind].dibujar(x.paso, ctx, x.m, y);
      y += x.m.alto;
    });
  }
}

window.MECANICAS = MECANICAS;
window.MECANICAS_HORIZONTAL = MECANICAS_HORIZONTAL;
