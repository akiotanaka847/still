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

/* ── Generar una promo ────────────────────────────────────────────────── */
async function labRenderRow(fields) {
  // templateForRow viene de promo.js: respeta la columna ORIENTACION de la
  // fila y cae a la plantilla del desplegable si no la trae.
  const template = await templateForRow(fields);
  const mapping = computeMapping(fields, template.fields);
  const { svgString, crop } = await buildSvgWithMaxFill(template, mapping, fields);
  const pngBlob = await renderToPng(svgString, crop.width, crop.height);
  return { template, svgString, crop, pngBlob };
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

    for (const row of LAB.rows) {
      const nombre = labRowName(row, LAB.outputs.length + 1);
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
          svg: new Blob([svgString], { type: "image/svg+xml" }),
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
    labBatchBtn.disabled = false;
    labGenerateBtn.disabled = false;
  });
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
  if (LAB.lastSvg) labDownload(new Blob([LAB.lastSvg], { type: "image/svg+xml" }), "promo.svg");
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
