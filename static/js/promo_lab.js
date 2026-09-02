/* =========================================================================
   Promos · Ingesta libre (experimental)

   Mismas promociones que el módulo de Promos, pero alimentadas desde
   cualquier origen (.json, .xlsx) en vez de solo Excel.

   Este archivo NO reimplementa nada del motor de render: reusa las funciones
   globales que ya define static/js/promo.js —`getTemplateById`,
   `templateForRow`, `computeMapping`, `buildSvgWithMaxFill`, `renderToPng`— y
   también su `state.templates`, que se carga al abrir la pestaña de Promos.
   Por eso este script debe cargarse DESPUÉS de promo.js.

   Estado propio en `LAB` para no pisar el `state` del módulo original.
   ========================================================================= */

const LAB = {
  rows: null,        // [{row_number, fields}] del origen leído
  sourceName: "",
  lastSvg: null,
  lastCrop: null,
  lastPng: null,
  outputs: [],       // [{name, png, svg, psd}] del último lote
};

const labInput = document.getElementById("labInput");
const labStatus = document.getElementById("labStatus");
const labLog = document.getElementById("labLog");
const labPreviewWrap = document.getElementById("labPreviewWrap");
const labSvgHost = document.getElementById("labSvgHost");
const labGenerateBtn = document.getElementById("labGenerateBtn");
const labBatchBtn = document.getElementById("labBatchBtn");
const labPsdChk = document.getElementById("labPsdChk");
const labZipBtn = document.getElementById("labZipBtn");
const labRowCount = document.getElementById("labRowCount");

function labLogMsg(msg) {
  if (!labLog) return;
  const time = new Date().toLocaleTimeString();
  labLog.textContent += `[${time}] ${msg}\n`;
  labLog.scrollTop = labLog.scrollHeight;
}

/* ── Lectura del origen ───────────────────────────────────────────────── */
if (labInput) {
  labInput.addEventListener("change", async () => {
    const file = labInput.files[0];
    if (!file) return;
    labStatus.textContent = "Leyendo...";
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch("/api/promo-lab/read-batch", { method: "POST", body: fd });
      if (!res.ok) {
        const e = await res.json().catch(() => ({}));
        throw new Error(e.detail || `Error ${res.status}`);
      }
      const json = await res.json();
      LAB.rows = json.rows;
      LAB.sourceName = json.source;
      labStatus.textContent = `✓ ${json.rows.length} fila(s)`;
      if (labRowCount) labRowCount.textContent = `${json.rows.length} promo(s) detectada(s)`;
      labLogMsg(`Origen leído: ${file.name} — ${json.rows.length} fila(s).`);

      // Informe del perfil de origen: cuántos registros se agruparon y, sobre
      // todo, qué imágenes faltan — esa lista es accionable, dice exactamente
      // qué subir para que las promos salgan completas.
      const info = json.info;
      if (info) {
        if (info.registros && info.promociones && info.registros !== info.promociones) {
          labLogMsg(`Se agruparon ${info.registros} registros en ${info.promociones} promociones (varios productos comparten promoción).`);
        }
        renderFaltantes(info.assets_faltantes || []);
      } else {
        renderFaltantes([]);
      }
      labGenerateBtn.disabled = false;
      labBatchBtn.disabled = false;
    } catch (e) {
      labStatus.textContent = "";
      labLogMsg(`ERROR leyendo el origen: ${e.message}`);
      LAB.rows = null;
      labGenerateBtn.disabled = true;
      labBatchBtn.disabled = true;
    }
  });
}

/* Imágenes que el origen pide y no están en la biblioteca. La promo se genera
   igual (sin ese elemento), así que esto no es un error: es la lista de lo que
   falta subir para que salgan completas. */
function renderFaltantes(faltantes) {
  const box = document.getElementById("labFaltantes");
  const list = document.getElementById("labFaltantesList");
  if (!box || !list) return;
  if (!faltantes.length) {
    box.style.display = "none";
    return;
  }
  list.innerHTML = "";
  faltantes.forEach((t) => {
    const chip = document.createElement("span");
    chip.className = "text-[10px] px-2 py-1 rounded-md glass text-amber-300";
    chip.textContent = t;
    list.appendChild(chip);
  });
  box.style.display = "block";
  labLogMsg(`⚠ Faltan ${faltantes.length} imagen(es) en la biblioteca: ${faltantes.join(", ")}`);
}

/* ── Colores del kit ──────────────────────────────────────────────────────
   La precedencia ya existía en el motor: el color del DATO pisa al de la
   plantilla. El kit se mete en el medio, quedando `dato → kit → plantilla`.

   Se aplica sobre una COPIA de la plantilla, no sobre la cacheada: si se
   mutara la original, cambiar de kit no podría volver atrás sin recargar. */
const COLOR_POR_CAMPO = { PESOS: "precio" };   // el resto de los textos: "texto"

function aplicarColoresDelKit(template) {
  const colores = (LAB.kit && LAB.kit.colors) || {};
  if (!Object.keys(colores).length) return template;

  const fields = template.fields.map((f) => {
    if (f.type === "image") return f;
    const clave = COLOR_POR_CAMPO[f.excel_column] || "texto";
    const color = colores[clave];
    if (!color) return f;
    const out = { ...f, color };
    // El "$" del precio es un elemento aparte con su propio color; si no se
    // ajusta también, queda de otro color que el número.
    if (f.prefix) out.prefix = { ...f.prefix, color };
    return out;
  });
  return { ...template, fields };
}

/* ── Generar una promo ────────────────────────────────────────────────── */
async function labRenderRow(fields) {
  // El motor avisa cosas mientras arma (por ejemplo, que una imagen que pide
  // el dato no está en la biblioteca). Se redirigen a ESTE panel mientras dura
  // el armado, y se devuelve el log a su dueño al terminar — si no, los avisos
  // caen en el panel del módulo de Excel, que acá no se ve.
  window.PROMO_LOG = labLogMsg;
  try {
    // templateForRow viene de promo.js: respeta la columna ORIENTACION de la
    // fila y cae a la plantilla del desplegable si no la trae.
    const template = aplicarColoresDelKit(await templateForRow(fields));
    const mapping = computeMapping(fields, template.fields);
    const { svgString, crop } = await buildSvgWithMaxFill(template, mapping, fields);
    const pngBlob = await renderToPng(svgString, crop.width, crop.height);
    return { template, svgString, crop, pngBlob };
  } finally {
    window.PROMO_LOG = null;
  }
}

if (labGenerateBtn) {
  labGenerateBtn.addEventListener("click", async () => {
    if (!LAB.rows || !LAB.rows.length) return;
    labGenerateBtn.disabled = true;
    try {
      labLogMsg("Generando la primera promo...");
      const { svgString, crop, pngBlob } = await labRenderRow(LAB.rows[0].fields);
      LAB.lastSvg = svgString;
      LAB.lastCrop = crop;
      LAB.lastPng = pngBlob;
      labSvgHost.innerHTML = svgString;
      labPreviewWrap.style.display = "block";
      labLogMsg(`Listo — ${crop.width}×${crop.height}px, PNG de ${(pngBlob.size / 1024).toFixed(0)} KB.`);
    } catch (e) {
      labLogMsg(`ERROR: ${e.message}`);
      console.error(e);
    } finally {
      labGenerateBtn.disabled = false;
    }
  });
}

/* ── Generar el lote completo ─────────────────────────────────────────── */
if (labBatchBtn) {
  labBatchBtn.addEventListener("click", async () => {
    if (!LAB.rows || !LAB.rows.length) return;
    labBatchBtn.disabled = true;
    labGenerateBtn.disabled = true;
    LAB.outputs = [];
    const t0 = performance.now();
    let ok = 0, err = 0;
    const total = LAB.rows.length;
    labSetProgress(0, total);

    for (const row of LAB.rows) {
      const nombre = labRowName(row, LAB.outputs.length + 1);
      // Ceder el hilo antes de cada fila: sin esto el navegador no repinta
      // durante el lote y la pantalla parece congelada, que es exactamente lo
      // que hace pensar que "no generó todas".
      await new Promise((r) => setTimeout(r, 0));
      labSetProgress(ok + err, total, nombre);
      try {
        const { svgString, crop, pngBlob } = await labRenderRow(row.fields);

        // Vista previa de la primera que sale bien: el lote tarda y si no no
        // se ve nada de lo que se está produciendo.
        if (!LAB.outputs.length) {
          labSvgHost.innerHTML = svgString;
          labPreviewWrap.style.display = "block";
          LAB.lastSvg = svgString; LAB.lastCrop = crop; LAB.lastPng = pngBlob;
        }

        let psdBlob = null;
        if (labPsdChk && labPsdChk.checked) {
          psdBlob = await labBuildPsd(svgString, crop, nombre);
        }

        LAB.outputs.push({
          name: nombre,
          png: pngBlob,
          svg: new Blob([svgParaDescargar(svgString)], { type: "image/svg+xml" }),
          psd: psdBlob,
        });
        ok++;
      } catch (e) {
        err++;
        labLogMsg(`Fila ${row.row_number}: ERROR — ${e.message}`);
        console.error(`Fila ${row.row_number}`, e);
      }
    }

    const secs = ((performance.now() - t0) / 1000).toFixed(1);
    labLogMsg(`Lote terminado: ${ok} OK, ${err} con error, en ${secs}s.`);
    labSetDone(ok, err, total, secs);
    labBatchBtn.disabled = false;
    labGenerateBtn.disabled = false;
  });
}

/* Avance visible del lote. Con 88 promos, sin esto la única señal es el log y
   cuesta distinguir "está trabajando" de "se colgó". */
function labSetProgress(hechas, total, nombre) {
  const box = document.getElementById("labProgress");
  const bar = document.getElementById("labProgressBar");
  const txt = document.getElementById("labProgressText");
  if (!box) return;
  box.style.display = "block";
  const pct = total ? Math.round((hechas / total) * 100) : 0;
  if (bar) bar.style.width = pct + "%";
  if (txt) {
    txt.textContent = nombre
      ? `Generando ${hechas + 1} de ${total} — ${nombre}`
      : `Generando ${hechas} de ${total}`;
  }
}

function labSetDone(ok, err, total, secs) {
  const bar = document.getElementById("labProgressBar");
  const txt = document.getElementById("labProgressText");
  if (bar) bar.style.width = "100%";
  if (txt) {
    txt.textContent = err
      ? `Listo: ${ok} de ${total} generadas, ${err} con error (${secs}s). Descargá el ZIP.`
      : `Listo: ${ok} de ${total} generadas en ${secs}s. Descargá el ZIP.`;
  }
}

function labRowName(row, fallbackIndex) {
  // Mismo criterio que el módulo original: si el origen trae un nombre corto,
  // se usa para el archivo; si no, el número de fila.
  const f = (row.fields || []).find((x) => normalize(x.column) === normalize("Nombre corto"));
  const raw = f && f.value ? String(f.value) : `fila_${row.row_number || fallbackIndex}`;
  return "promo_" + raw
    .normalize("NFKD").replace(/[̀-ͯ]/g, "")
    .replace(/[^A-Za-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 60);
}

/* ── Exportar a PSD por capas (reusa rasterizeLayers de promo.js) ─────── */
async function labBuildPsd(svgString, crop, nombre) {
  const layers = await rasterizeLayers(svgString, crop.width, crop.height);
  if (!layers.length) throw new Error("No se pudo separar la plantilla en capas");
  const form = new FormData();
  layers.forEach((l, i) => form.append("layers", l.blob, `layer_${i}.png`));
  form.append("names", JSON.stringify(layers.map((l) => l.name)));
  form.append("filename", nombre);
  const res = await fetch("/api/promo/psd", { method: "POST", body: form });
  if (!res.ok) {
    const e = await res.json().catch(() => ({}));
    throw new Error(e.detail || `PSD: error ${res.status}`);
  }
  return await res.blob();
}

/* ── Descargas ────────────────────────────────────────────────────────── */

/* svgParaDescargar() se define en promo.js (mismo arreglo, mismo bug) y se
   reusa acá — este archivo se carga después. */

function labDownload(blob, filename) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
}

document.getElementById("labPngBtn")?.addEventListener("click", () => {
  if (LAB.lastPng) labDownload(LAB.lastPng, "promo.png");
});

document.getElementById("labSvgBtn")?.addEventListener("click", () => {
  if (LAB.lastSvg) labDownload(new Blob([svgParaDescargar(LAB.lastSvg)], { type: "image/svg+xml" }), "promo.svg");
});

document.getElementById("labPsdBtn")?.addEventListener("click", async (ev) => {
  if (!LAB.lastSvg || !LAB.lastCrop) return;
  const btn = ev.currentTarget;
  btn.disabled = true;
  const original = btn.textContent;
  btn.textContent = "Armando PSD...";
  try {
    labLogMsg("Rasterizando una capa por campo...");
    const blob = await labBuildPsd(LAB.lastSvg, LAB.lastCrop, "promo");
    labDownload(blob, "promo.psd");
    labLogMsg(`PSD descargado (${(blob.size / 1024).toFixed(0)} KB).`);
  } catch (e) {
    labLogMsg(`ERROR al armar el PSD: ${e.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
});

if (labZipBtn) {
  labZipBtn.addEventListener("click", async () => {
    if (!LAB.outputs.length) return;
    labZipBtn.disabled = true;
    try {
      const form = new FormData();
      const names = [];
      for (const o of LAB.outputs) {
        form.append("files", o.png, `${o.name}.png`); names.push(`${o.name}.png`);
        form.append("files", o.svg, `${o.name}.svg`); names.push(`${o.name}.svg`);
        if (o.psd) { form.append("files", o.psd, `${o.name}.psd`); names.push(`${o.name}.psd`); }
      }
      form.append("names", JSON.stringify(names));
      const res = await fetch("/api/promo/zip", { method: "POST", body: form });
      if (!res.ok) throw new Error(`Error ${res.status}`);
      labDownload(await res.blob(), "promos.zip");
      labLogMsg(`ZIP descargado (${LAB.outputs.length} promos).`);
    } catch (e) {
      labLogMsg(`ERROR al armar el ZIP: ${e.message}`);
    } finally {
      labZipBtn.disabled = false;
    }
  });
}

/* =========================================================================
   Kit de marca: imágenes (con tipo), colores y tipografía

   Las tres cosas varían por el mismo eje —el cliente— así que se administran
   juntas. Ver promo_kits.py para el modelo de datos.

   El TIPO de imagen no es decorativo: la búsqueda es por texto, así que sin
   tipo una marca "NUEVO" y una placa "NUEVO" colisionan y gana la que quede
   primera alfabéticamente. Con tipo, PROVEEDOR busca solo entre marcas y
   TEXTO APOYO solo entre placas.
   ========================================================================= */
const KIT_ID = "base";   // por ahora un solo kit; el modelo ya soporta varios

const assetInput = document.getElementById("assetInput");
const assetType = document.getElementById("assetType");
const assetList = document.getElementById("assetList");
const assetCount = document.getElementById("assetCount");
const fontInput = document.getElementById("fontInput");
const fontWeight = document.getElementById("fontWeight");
const fontList = document.getElementById("fontList");

const TIPO_LABEL = { callout: "Callout", pleca: "Pleca", marca: "Marca" };
const TIPO_COLOR = {
  callout: "text-indigo-300",
  pleca: "text-cyan-300",
  marca: "text-purple-300",
};

async function kitFetch(url, opts) {
  const res = await fetch(url, opts);
  if (!res.ok) {
    const e = await res.json().catch(() => ({}));
    throw new Error(e.detail || `Error ${res.status}`);
  }
  return res.json();
}

function renderAssets(assets) {
  if (!assetList) return;
  assetList.innerHTML = "";

  const propias = assets.filter((a) => !a.system);
  const sistema = assets.filter((a) => a.system);

  if (!propias.length) {
    const vacio = document.createElement("p");
    vacio.className = "text-[11px] text-gray-500 py-2";
    vacio.textContent = "Todavía no subiste ninguna imagen. Elegí el tipo arriba y subí tus plecas, callouts y marcas. Si una promo pide una que no está, se genera sin ese elemento.";
    assetList.appendChild(vacio);
  }

  propias.forEach((a) => {
    const row = document.createElement("div");
    row.className = "flex items-center gap-2 px-2 py-1.5 rounded-lg glass";

    const tipo = document.createElement("select");
    tipo.className = `text-[10px] font-semibold bg-transparent ${TIPO_COLOR[a.type] || ""}`;
    tipo.style.cssText = "color-scheme:dark;border:none;outline:none;cursor:pointer";
    Object.entries(TIPO_LABEL).forEach(([v, label]) => {
      const o = document.createElement("option");
      o.value = v; o.textContent = label; o.selected = v === a.type;
      tipo.appendChild(o);
    });
    tipo.addEventListener("change", async () => {
      try {
        const fd = new FormData(); fd.append("type", tipo.value);
        const r = await kitFetch(
          `/api/promo-lab/kits/${KIT_ID}/assets/${encodeURIComponent(a.file)}`,
          { method: "PATCH", body: fd });
        renderAssets(r.assets);
        labLogMsg(`"${a.file}" ahora es ${TIPO_LABEL[tipo.value]}.`);
      } catch (e) { labLogMsg(`ERROR cambiando el tipo: ${e.message}`); }
    });

    const nombre = document.createElement("span");
    nombre.className = "text-[11px] text-gray-500 flex-1 truncate";
    nombre.textContent = a.file;

    const borrar = document.createElement("button");
    borrar.className = "text-[11px] text-gray-500 hover:text-red-400 px-1";
    borrar.textContent = "✕";
    borrar.title = `Borrar ${a.file}`;
    borrar.addEventListener("click", async () => {
      // Borrado real de un archivo: se confirma antes, no se puede deshacer.
      if (!confirm(`¿Borrar "${a.file}" de la biblioteca?\n\nNo se puede deshacer.`)) return;
      try {
        const r = await kitFetch(
          `/api/promo-lab/kits/${KIT_ID}/assets/${encodeURIComponent(a.file)}`,
          { method: "DELETE" });
        renderAssets(r.assets);
        if (assetCount) assetCount.textContent = `(${r.assets.length})`;
        labLogMsg(`"${a.file}" borrado.`);
      } catch (e) { labLogMsg(`ERROR al borrar: ${e.message}`); }
    });

    row.append(tipo, nombre, borrar);
    assetList.appendChild(row);
  });

  // Las del sistema van aparte y sin borrar: no las nombran los datos, las
  // pone el layout, así que no son biblioteca del cliente sino de la plantilla.
  if (sistema.length) {
    const t = document.createElement("p");
    t.className = "text-[10px] text-gray-500 pt-3 pb-1";
    t.textContent = "Banners que dibujan solas las promos de \"lleva y llévate\". No los nombran tus datos, no se borran:";
    assetList.appendChild(t);
    const cont = document.createElement("div");
    cont.className = "flex flex-wrap gap-1.5";
    sistema.forEach((a) => {
      const chip = document.createElement("span");
      chip.className = "text-[10px] px-2 py-1 rounded-md text-gray-500";
      chip.style.cssText = "background:rgba(255,255,255,.03);border:1px dashed rgba(255,255,255,.12)";
      chip.textContent = a.file;
      cont.appendChild(chip);
    });
    assetList.appendChild(cont);
  }

  // El conteo del encabezado cuenta SOLO las del usuario: son las que importan.
  if (assetCount) assetCount.textContent = `(${propias.length})`;
}

function renderFonts(font) {
  if (!fontList) return;
  fontList.innerHTML = "";
  const pesos = (font && font.weights) || {};
  if (!Object.keys(pesos).length) {
    const p = document.createElement("span");
    p.className = "text-[10px] text-gray-500";
    p.textContent = "Sin fuentes propias — se usan las de la plantilla.";
    fontList.appendChild(p);
    return;
  }
  Object.entries(pesos).sort((a, b) => b[0] - a[0]).forEach(([w, file]) => {
    const chip = document.createElement("span");
    chip.className = "text-[10px] px-2 py-1 rounded-md glass text-gray-500 flex items-center gap-1.5";
    chip.textContent = `${w} · ${file}`;
    const x = document.createElement("button");
    x.className = "hover:text-red-400"; x.textContent = "✕";
    x.addEventListener("click", async () => {
      try {
        const r = await kitFetch(`/api/promo-lab/kits/${KIT_ID}/fonts/${w}`, { method: "DELETE" });
        renderFonts(r.font);
        labLogMsg(`Fuente del peso ${w} eliminada.`);
      } catch (e) { labLogMsg(`ERROR: ${e.message}`); }
    });
    chip.appendChild(x);
    fontList.appendChild(chip);
  });
}

/* Los <input type=color> solo entienden hex; el motor usa rgb(). */
const hexARgb = (h) => {
  const m = /^#?([0-9a-f]{6})$/i.exec(h || "");
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return `rgb(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255})`;
};
const rgbAHex = (c) => {
  const m = /rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)/.exec(c || "");
  if (!m) return /^#[0-9a-f]{6}$/i.test(c || "") ? c : null;
  return "#" + [1, 2, 3].map((i) => (+m[i]).toString(16).padStart(2, "0")).join("");
};

async function guardarColores() {
  const colores = {};
  if (LAB.colorOn.precio) colores.precio = hexARgb(document.getElementById("colorPrecio").value);
  if (LAB.colorOn.texto) colores.texto = hexARgb(document.getElementById("colorTexto").value);
  try {
    const r = await kitFetch(`/api/promo-lab/kits/${KIT_ID}/colors`, {
      method: "PUT",
      body: (() => { const f = new FormData(); f.append("colors", JSON.stringify(colores)); return f; })(),
    });
    LAB.kit.colors = r.colors;
    labLogMsg(Object.keys(r.colors).length
      ? `Colores del kit: ${Object.entries(r.colors).map(([k, v]) => `${k} ${v}`).join(", ")}. Volvé a generar para verlos.`
      : "Colores del kit vaciados — se usan los de la plantilla.");
  } catch (e) { labLogMsg(`ERROR guardando colores: ${e.message}`); }
}

LAB.kit = { colors: {}, font: {} };
LAB.colorOn = { precio: false, texto: false };

window.promoKitInit = async function () {
  try {
    const kit = await kitFetch(`/api/promo-lab/kits/${KIT_ID}`);
    LAB.kit = { colors: kit.colors || {}, font: kit.font || {} };
    // Banners fijos declarados por el kit: los lee systemAsset() de promo.js,
    // que resuelve plantilla -> kit -> valor por defecto.
    window.PROMO_KIT_ASSETS = kit.system_assets || {};
    renderAssets(kit.assets || []);
    renderFonts(kit.font);
    // Reflejar los colores guardados en los selectores
    ["precio", "texto"].forEach((k) => {
      const hex = rgbAHex(LAB.kit.colors[k]);
      if (hex) {
        const el = document.getElementById(k === "precio" ? "colorPrecio" : "colorTexto");
        if (el) el.value = hex;
        LAB.colorOn[k] = true;
      }
    });
  } catch (e) {
    console.error("No se pudo cargar el kit", e);
  }
};

if (assetInput) {
  assetInput.addEventListener("change", async () => {
    const files = Array.from(assetInput.files || []);
    if (!files.length) return;
    const form = new FormData();
    files.forEach((f) => form.append("files", f, f.name));
    form.append("type", assetType ? assetType.value : "callout");
    try {
      const r = await kitFetch(`/api/promo-lab/kits/${KIT_ID}/assets`, { method: "POST", body: form });
      if (r.saved.length) labLogMsg(`✓ ${r.saved.length} imagen(es) subida(s) como ${TIPO_LABEL[assetType.value]}: ${r.saved.join(", ")}`);
      (r.skipped || []).forEach((s) => labLogMsg(`⚠ Se omitió "${s.filename}": ${s.reason}`));
      renderAssets(r.assets);
    } catch (e) {
      labLogMsg(`ERROR subiendo imágenes: ${e.message}`);
    } finally { assetInput.value = ""; }
  });
}

if (fontInput) {
  fontInput.addEventListener("change", async () => {
    const f = fontInput.files[0];
    if (!f) return;
    const form = new FormData();
    form.append("file", f, f.name);
    form.append("weight", fontWeight ? fontWeight.value : "400");
    try {
      const r = await kitFetch(`/api/promo-lab/kits/${KIT_ID}/fonts`, { method: "POST", body: form });
      LAB.kit.font = r.font;
      renderFonts(r.font);
      labLogMsg(`Fuente "${f.name}" cargada como peso ${fontWeight.value}.`);
    } catch (e) {
      labLogMsg(`ERROR subiendo la fuente: ${e.message}`);
    } finally { fontInput.value = ""; }
  });
}

["precio", "texto"].forEach((k) => {
  const input = document.getElementById(k === "precio" ? "colorPrecio" : "colorTexto");
  const reset = document.getElementById(k === "precio" ? "colorPrecioReset" : "colorTextoReset");
  if (input) input.addEventListener("change", () => { LAB.colorOn[k] = true; guardarColores(); });
  if (reset) reset.addEventListener("click", () => { LAB.colorOn[k] = false; guardarColores(); });
});
