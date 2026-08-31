/** Config mínimo para regenerar static/css/tailwind.css a partir del HTML real.
 * El archivo original (commit e393730, 2026-08-26) se generó una sola vez y
 * nunca se volvió a compilar — features agregadas después quedaron con
 * clases de Tailwind "fantasma" (existen en el HTML pero no en el CSS
 * compilado, sin error visible, solo se ignoran). Este config no agrega
 * tema custom: los colores de marca ya viven como CSS plano en el <style>
 * inline de index.html, no como theme de Tailwind. */
module.exports = {
  content: ["./static/index.html"],
  theme: { extend: {} },
  plugins: [],
};
