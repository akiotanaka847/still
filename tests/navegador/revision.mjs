/**
 * Revisión de la interfaz en un navegador de verdad, antes de desplegar.
 *
 * Existe en el repo y no en /tmp porque estas comprobaciones han encontrado
 * cosas que ni pytest ni el detector de español ven —un panel insertado fuera
 * de <main> que renderizaba en blanco, una clase de Tailwind sin compilar que
 * dejaba Settings pegado al borde, peticiones duplicadas tras iniciar sesión—
 * y se perdieron dos veces por vivir en un directorio temporal.
 *
 *   node tests/navegador/revision.mjs                 # contra localhost:8000
 *   node tests/navegador/revision.mjs https://...     # contra otro sitio
 *
 * Necesita un servidor corriendo y el usuario de prueba. Sale con código 0
 * solo si todo pasa, así que sirve para encadenar antes de un despliegue.
 */
import { chromium } from '../../node_modules/playwright/index.mjs';

const BASE = process.argv[2] || 'http://127.0.0.1:8000';
const USUARIO = process.env.STILL_TEST_USER || 'testgen_still_ai';
const CLAVE = process.env.STILL_TEST_PASS || 'testpass123';

let fallos = 0;
const chk = (que, ok, extra = '') => {
  if (!ok) fallos++;
  console.log(`  ${ok ? '✓' : '✗ FALLA'} ${que}${extra ? ' · ' + extra : ''}`);
};

const navegador = await chromium.launch();
const p = await navegador.newPage({ viewport: { width: 1500, height: 1100 } });

const erroresJs = [];
const peticionesMal = [];
p.on('pageerror', (e) => erroresJs.push(e.message));
p.on('response', (r) => {
  // El 401 de /auth/me antes de iniciar sesión es correcto: checkAuth TIENE
  // que preguntar para saber si hay sesión.
  if (r.status() >= 400 && !r.url().includes('auth/me')) {
    peticionesMal.push(`${r.status()} ${r.url().split('/api/')[1] || r.url()}`);
  }
});

await p.goto(BASE, { waitUntil: 'networkidle' });
await p.evaluate(() => document.fonts.ready);
await p.waitForTimeout(1200);

console.log('── LOGIN ──');
const login = await p.evaluate(() => ({
  frase: document.querySelector('#login-overlay h1')?.innerText || '',
  lockup: !!document.querySelector('#login-overlay img[src*="still-lockup"]'),
  titular: getComputedStyle(document.querySelector('#login-overlay h1')).fontFamily,
  cuerpo: getComputedStyle(document.body).fontFamily,
}));
chk('la frase de marca', login.frase.replace(/\n/g, ' ') === 'Where creativity scales.', JSON.stringify(login.frase));
chk('el lockup real del logo', login.lockup);
chk('titulares en Goldplay Alt', login.titular.includes('Goldplay Alt'));
chk('cuerpo en Goldplay', login.cuerpo.includes('Goldplay') && !login.cuerpo.includes('Alt'));
chk('sin rastro de Inter', !login.titular.includes('Inter') && !login.cuerpo.includes('Inter'));

await p.evaluate(async ([u, c]) => {
  await fetch('/api/auth/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: u, password: c }),
  });
}, [USUARIO, CLAVE]);
await p.reload({ waitUntil: 'networkidle' });
await p.evaluate(() => document.fonts.ready);
await p.waitForTimeout(2500);
peticionesMal.length = 0;   // lo de antes de la sesión ya se juzgó

console.log('\n── SIDEBAR ──');
const nav = await p.evaluate(() => {
  const aside = document.querySelector('aside');
  const menu = aside.querySelector('nav');
  const set = document.getElementById('nav-settings');
  return {
    // Las secciones de Still y los plugins de terceros se piden por separado:
    // si se mezclan, anadir un plugin rompe la comprobacion del menu sin que
    // nada este mal. La clase plugin-item es lo que los separa.
    items: [...menu.querySelectorAll('.nav-item:not(.plugin-item) span')].map((s) => s.textContent),
    plugins: [...menu.querySelectorAll('.plugin-item')].map((a) => {
      const marco = document.getElementById('frame-' + (a.id || '').replace('nav-', ''));
      return {
        nombre: a.querySelector('span')?.textContent,
        // Se cargan al abrirlos, no antes: traer dos aplicaciones de terceros
        // en cada carga de Still seria caro y casi siempre inutil.
        perezoso: !!marco && !!marco.dataset.src,
        destino: marco ? marco.dataset.src : null,
        // Sin allow-top-navigation el sitio incrustado no puede llevarse la
        // pestana entera de Still a otra parte.
        noSeLlevaLaPestana: !!marco && !(marco.getAttribute('sandbox') || '').includes('top-navigation'),
      };
    }),
    settingsEnMenu: !!menu.querySelector('#nav-settings'),
    aireAbajo: Math.round(aside.getBoundingClientRect().bottom - set.getBoundingClientRect().bottom),
    separador: getComputedStyle(set.parentElement).borderTopWidth,
  };
});
chk('el menú deja las 4 de uso diario', nav.items.join(',') === 'Home,Create,Batch,Projects', nav.items.join(', '));
chk('Settings fuera del menú', !nav.settingsEnMenu);
chk('Settings al pie, con aire', nav.aireAbajo > 4 && nav.aireAbajo < 40, nav.aireAbajo + 'px del borde');
chk('con separador arriba', nav.separador !== '0px', nav.separador);
chk('los plugins están', nav.plugins.length === 2,
  nav.plugins.map((x) => x.nombre).join(', '));
for (const pl of nav.plugins) {
  chk(`${pl.nombre}: carga perezosa y no se lleva la pestaña`,
    pl.perezoso && pl.noSeLlevaLaPestana && /^https:\/\//.test(pl.destino || ''),
    (pl.destino || '').slice(8, 46));
}

console.log('\n── HOME ──');
const home = await p.evaluate(() => {
  const arriba = document.querySelector('#panel-home section.grid');
  const hijos = [...arriba.children];
  const rec = document.getElementById('home-recent');
  return {
    columnas: getComputedStyle(arriba).gridTemplateColumns.split(' ').length,
    tarjetasArriba: hijos.length,
    alturasIguales: new Set(hijos.map((h) => Math.round(h.getBoundingClientRect().height))).size === 1,
    llegaAlBorde: Math.abs(hijos[hijos.length - 1].getBoundingClientRect().right
      - arriba.getBoundingClientRect().right) < 4,
    proyectos: rec.children.length,
    borradores: rec.querySelectorAll('.draft-bg').length,
    etiquetasDraft: [...rec.querySelectorAll('span')].filter((s) => s.textContent === 'Draft').length,
    fotosProducto: rec.querySelectorAll('img[src*="product-thumb"]').length,
    checkerEnBorrador: [...rec.querySelectorAll('.checker-bg')]
      .some((e) => !e.querySelector('img[src*="download"]')),
  };
});
chk('3 columnas y 3 tarjetas', home.columnas === 3 && home.tarjetasArriba === 3,
  `${home.columnas} cols · ${home.tarjetasArriba} tarjetas`);
chk('las 3 a la misma altura', home.alturasIguales);
chk('la fila llega al borde derecho', home.llegaAlBorde);
chk('proyectos recientes pintados', home.proyectos > 0, home.proyectos + '');
chk('los borradores muestran sus productos',
  home.borradores === 0 || home.fotosProducto > 0,
  `${home.borradores} borradores · ${home.fotosProducto} miniaturas`);
chk('etiqueta Draft en cada borrador', home.etiquetasDraft === home.borradores,
  `${home.etiquetasDraft}/${home.borradores}`);
chk('sin checkerboard en borradores', !home.checkerEnBorrador);
chk('titulares del Home en Goldplay Alt',
  await p.evaluate(() => getComputedStyle(document.querySelector('#panel-home h1')).fontFamily.includes('Goldplay Alt')));

const pluginsHome = await p.evaluate(() => {
  const secs = [...document.querySelectorAll('#panel-home section')];
  const ultima = secs[secs.length - 1];
  const cards = [...ultima.querySelectorAll('button')];
  const grid = ultima.querySelector('.grid');
  return {
    titulo: ultima.querySelector('h2')?.textContent,
    nombres: cards.map((c) => c.querySelector('h3')?.textContent),
    // Tantas columnas como tarjetas: declarar de mas es lo que dejaba el
    // hueco de la derecha que ya hubo que arreglar una vez.
    columnas: grid ? getComputedStyle(grid).gridTemplateColumns.split(' ').length : 0,
    mismaAltura: new Set(cards.map((c) => Math.round(c.getBoundingClientRect().height))).size === 1,
  };
});
chk('los plugins también están en el Home',
  pluginsHome.titulo === 'Plugins' && pluginsHome.nombres.length === 2,
  pluginsHome.nombres.join(', '));
chk('sin columnas de mas ni tarjetas descuadradas',
  pluginsHome.columnas === pluginsHome.nombres.length && pluginsHome.mismaAltura,
  `${pluginsHome.columnas} columnas para ${pluginsHome.nombres.length} tarjetas`);

console.log('\n── LAS DEMÁS PESTAÑAS ──');
for (const [tab, panel] of [['bodegon', 'panel-bodegon'], ['batch', 'panel-batch'],
                            ['projects', 'panel-projects'], ['settings', 'panel-settings']]) {
  await p.evaluate((t) => switchTab(t), tab);
  await p.waitForTimeout(1400);
  const visible = await p.evaluate((id) => {
    const e = document.getElementById(id);
    return !!e && !e.classList.contains('hidden') && e.getBoundingClientRect().height > 50;
  }, panel);
  chk(`${tab} abre y tiene contenido`, visible);
}

console.log('\n── IDIOMA ──');
for (const tab of ['home', 'bodegon', 'batch', 'projects', 'settings']) {
  await p.evaluate((t) => switchTab(t), tab);
  await p.waitForTimeout(700);
  const es = await p.evaluate(() => {
    const txt = document.body.innerText;
    // Palabras de clase cerrada: si aparecen, hay español de verdad, no un
    // nombre de producto con acento.
    return /\b(el|la|los|las|una|para|con|sin|desde|hasta|pero|porque|cuando|donde|esto|esta|estos|listos|sueltos)\b/i
      .test(txt) || /[¿¡]/.test(txt);
  });
  chk(`${tab} sin español`, !es);
}

console.log('');
chk('sin errores de JavaScript', erroresJs.length === 0, erroresJs.slice(0, 2).join(' | '));
chk('sin peticiones fallidas con sesión', peticionesMal.length === 0, peticionesMal.slice(0, 4).join(', '));

console.log(`\n  ${fallos === 0 ? '✓ TODO EN VERDE' : '✗ ' + fallos + ' FALLOS'}`);
await navegador.close();
process.exit(fallos === 0 ? 0 : 1);
