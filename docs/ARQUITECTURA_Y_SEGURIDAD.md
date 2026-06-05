# StillAI — Arquitectura, funcionamiento y seguridad

> Documento para revisión técnica/de negocio. Estado: **prototipo funcional avanzado (MVP+)**.
> Apto para demos y uso interno; el **hardening de seguridad a nivel de código ya está aplicado** —
> lo pendiente es de despliegue (HTTPS) y de plataforma (base de datos, cuentas).
>
> **Versión:** v2.1.0 · **Fecha:** 2026-06-04 · **Repositorio:** `still` (GitHub privado).

---

## 1. Resumen ejecutivo

StillAI genera **bodegones de producto** (composiciones con fondo transparente) de forma
automática: el usuario sube imágenes, la IA decide el orden y el tamaño real de cada producto,
y el sistema entrega un PNG listo para tienda, catálogo o redes. Incluye un módulo **por lotes**
(carpetas → muchos bodegones) y **login** de usuario.

- **Madurez:** prototipo funcional, una sola instancia, almacenamiento local.
- **Seguridad:** base correcta (contraseñas hasheadas, sesiones firmadas, rutas protegidas)
  **más el hardening OWASP aplicado en v2.1.0** (rate limiting, headers de seguridad, CORS
  restringido, límites de subida, errores genéricos, config por entorno). Lo que **resta** es de
  **despliegue** (HTTPS/TLS, rotación de secretos) y de **plataforma** (base de datos, cuentas).
- **Para ofrecerlo a clientes:** falta cerrar el despliegue seguro (Fase A) y cuentas/datos
  (Fase B), más decisiones sobre privacidad, porque las imágenes se procesan con proveedores de
  IA externos.

---

## 2. Stack tecnológico

| Capa | Tecnología |
|---|---|
| Backend / API | **Python 3.14 + FastAPI + Uvicorn** (ASGI) |
| Sesiones / auth | Starlette **SessionMiddleware** (cookie firmada, `itsdangerous`) + **PBKDF2-HMAC-SHA256** |
| Procesamiento de imagen | **Pillow** (composición, recorte al contenido, sombras de contacto) |
| IA (orden + tamaños) | Configurable: **Groq** (Llama 4 Scout), **Google Gemini** (2.5 Flash), **Anthropic Claude** |
| Frontend | **HTML + Vanilla JS + Tailwind CSS** (sin framework) · render con `<canvas>` |
| Almacenamiento | **Sistema de archivos local** (`sessions/`, `users.json`) — sin base de datos aún |
| Empaquetado | ZIP nativo (`zipfile`) para descargas por lote |

> No hay framework de frontend (React/Vue), ni base de datos, ni colas todavía — es intencional
> para el MVP. El roadmap (ver `ROADMAP_PLATAFORMA.md`) contempla DB, colas y nube para escalar.
>
> **Nota (v2.1.0):** el antiguo módulo de reemplazo de Smart Objects en PSD/PSB (PhotoshopAPI) fue
> **retirado** del producto. StillAI se centra ahora en la generación de bodegones con fondo
> transparente; ya no depende de `PhotoshopAPI`.

---

## 3. Cómo funciona (flujo)

```
Usuario → Login (sesión por cookie)
   │
   ├─ Bodegón individual:
   │     sube productos → recorte al contenido real → la IA analiza (orden + tamaño real)
   │     → motor de layout compone (pirámide, profundidad, sombras) → PNG transparente
   │
   └─ Por lotes:
         sube carpetas (cada subcarpeta = un grupo) → genera un bodegón por grupo
         → descarga todo en ZIP
```

- **La IA solo decide el ORDEN y el TAMAÑO** de los productos; la composición la hace un motor
  geométrico propio (determinista). El fondo siempre es transparente.
- Cada trabajo vive en una **sesión con UUID** en disco; limpieza automática a las 24 h.

---

## 4. Estado de seguridad (evaluación honesta)

### ✅ Lo que YA está bien
- **Contraseñas:** nunca en texto plano. PBKDF2-HMAC-SHA256, 200 000 iteraciones, salt por usuario.
- **Sesiones:** cookie **firmada** (no manipulable), `HttpOnly`, `SameSite=Lax`, caducidad 7 días.
- **Autorización real:** las rutas de generación (`/api/bodegon`, `/api/batch`, …) devuelven **401**
  sin sesión — no es solo ocultar la UI.
- **Validación de entrada básica:** IDs de sesión validados por regex; nombres de archivo
  saneados (sin *path traversal*); extensiones de imagen filtradas.
- **Secretos fuera del repo:** `.env`, `users.json`, `.secret` están en `.gitignore`.

### ✅ Hardening aplicado en v2.1.0 (OWASP, "audit-ready")
Implementado a nivel de código, manteniendo la app 100 % funcional, y **verificado en ejecución**
(headers presentes, CORS rechaza orígenes no permitidos, política de contraseña activa, rate
limiting devuelve `429` tras 8 intentos):
- **Rate limiting** anti fuerza-bruta en login/registro (por IP y por usuario) → `429` al exceder.
- **Headers de seguridad** en todas las respuestas: `Content-Security-Policy`, `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy`, `Permissions-Policy` (y `HSTS` cuando hay HTTPS).
- **Cookie de sesión `Secure` configurable** (`COOKIE_SECURE=true` en producción) + HSTS.
- **CORS restringido** a orígenes permitidos (ya no `*`), con credenciales.
- **Límites de subida**: tamaño por archivo (`MAX_FILE_MB`) y cantidad por grupo (`MAX_FILES`) → `413`.
- **Política de contraseña** (mín. 8) y **registro configurable** (`REGISTRATION_OPEN=false` lo cierra).
- **Errores genéricos** en producción (no se filtran trazas internas; detalle solo con `DEBUG=true`).
- **Config por entorno** documentada en `.env.example` (sin secretos).

### 📊 Estado por control de seguridad
| Control | Riesgo si falta | Estado |
|---|---|---|
| HTTPS/TLS | credenciales viajan sin cifrar | ⏳ **Pendiente** (despliegue) — la app ya soporta `COOKIE_SECURE`+HSTS al activarlo |
| Cookie `Secure` | depende de no-HTTPS | ✅ Configurable (`COOKIE_SECURE=true`) — v2.1.0 |
| Rate limiting (login/registro) | fuerza bruta de login | ✅ Hecho — v2.1.0 |
| Rate limiting en endpoints de IA | abuso de costos de IA | ⏳ **Pendiente** (hoy solo login/registro) |
| Registro abierto | cualquiera crea cuenta | ✅ Configurable (`REGISTRATION_OPEN=false`) — v2.1.0 |
| Límite de tamaño/cantidad de subidas | DoS por archivos enormes/masivos | ✅ Hecho (`MAX_FILE_MB`/`MAX_FILES`) — v2.1.0 |
| CORS restringido | exposición cross-origin | ✅ Hecho (`ALLOWED_ORIGINS`) — v2.1.0 |
| Headers de seguridad (CSP, etc.) | clickjacking / XSS / sniffing | ✅ Hecho — v2.1.0 |
| Errores genéricos en producción | fuga de información interna | ✅ Hecho (`DEBUG=false`) — v2.1.0 |
| Política de contraseña | cuentas débiles | ✅ Mín. 8 — v2.1.0 (fuerza tipo zxcvbn pendiente) |
| Verificación de email / reset / 2FA | recuperación y robo de cuenta | ⏳ **Pendiente** (Fase B) |
| Escaneo de dependencias (pip-audit) | CVEs en librerías | ⏳ **Pendiente** (CI) |
| Base de datos (vs archivos) | integridad/escala de cuentas | ⏳ **Pendiente** (Fase B) |
| Rotación de secretos | claves de dev expuestas | 🔴 **Acción inmediata** (ver abajo) |

### 🔑 Acción inmediata
- **Rotar las API keys** de Groq/Anthropic que se usaron en desarrollo (se compartieron en texto
  durante la construcción). Generar nuevas y guardarlas solo en `.env`/gestor de secretos.
- En producción: poner la app detrás de **HTTPS** (reverse proxy) y activar `COOKIE_SECURE=true`,
  `REGISTRATION_OPEN=false`, y `ALLOWED_ORIGINS` con el dominio real.

---

## 5. Privacidad de datos (importante para ofrecerlo)

Las imágenes de producto se envían a **proveedores de IA externos** (Groq / Google / Anthropic)
para el análisis de orden y tamaño. Implicaciones:
- Los datos del cliente **salen de nuestra infraestructura** hacia terceros.
- Hay que revisar los **términos de retención** de cada proveedor (varios ofrecen *zero data
  retention* para empresas).
- Para clientes con datos sensibles o normativa (GDPR/LOPD), se debe **informar y obtener
  consentimiento**, o usar un modo "solo geométrico" (sin IA, sin salida de datos), que ya existe.

---

## 6. ¿Se puede "certificar"? Qué significa y cómo llegamos

No existe una "certificación" única; depende del objetivo comercial. Opciones reales:

| Objetivo | Qué es | Esfuerzo |
|---|---|---|
| **OWASP ASVS** | Estándar técnico de seguridad de apps (benchmark) | Implementable por nosotros |
| **GDPR / LOPD** | Cumplimiento de privacidad (datos personales/cliente) | Técnico + legal |
| **SOC 2 Tipo II** | Auditoría externa de controles operativos | Auditor + 6-12 meses de operación |
| **ISO 27001** | Sistema de gestión de seguridad de la información | Organizacional + auditoría |

**Importante:** SOC 2 / ISO los **emite un auditor externo** tras documentar políticas y *operarlas*
en el tiempo — no es algo que el código "tenga" de un día para otro. Lo que **sí podemos hacer
nosotros** es dejar el producto **"audit-ready"**: seguro por diseño, siguiendo OWASP, de modo que
una auditoría posterior sea rápida.

---

## 7. Plan de endurecimiento (para quedar "audit-ready")

**Fase A — Seguridad base** (mayormente cubierta en v2.1.0 a nivel de código)
1. ⏳ HTTPS/TLS obligatorio + cookies `Secure` — *soporte listo (`COOKIE_SECURE`); falta el despliegue tras HTTPS*.
2. 🟡 Rate limiting — ✅ login/registro hecho; ⏳ falta extenderlo a los **endpoints de IA**.
3. ✅ Límites de tamaño y cantidad de subidas — **hecho** (`MAX_FILE_MB`/`MAX_FILES`).
4. ✅ CORS restringido al dominio real — **hecho** (`ALLOWED_ORIGINS`).
5. ⏳ Rotación de secretos + gestor de secretos (no `.env` plano en prod) — **pendiente (acción inmediata)**.
6. ⏳ Escaneo de dependencias (pip-audit) en CI — pendiente.

> Extras ya hechos en v2.1.0 fuera de la lista original: headers de seguridad (CSP/HSTS/etc.),
> política de contraseña (mín. 8), registro cerrable, errores genéricos en producción.

**Fase B — Cuentas y datos (1-2 semanas)**
7. Base de datos (Postgres) para usuarios/proyectos en vez de archivos.
8. Política de contraseña fuerte + verificación de email + reset + (opcional) 2FA.
9. Registro controlado (invitaciones / aprobación).
10. Aviso de privacidad y consentimiento sobre el uso de IA externa.

**Fase C — Operación y cumplimiento (continuo)**
11. Logs de auditoría (quién hizo qué).
12. Backups y plan de recuperación.
13. Documentar políticas (acceso, incidentes) → base para SOC 2 / ISO.
14. Pentest externo antes del lanzamiento comercial.

---

## 8. Conclusión

StillAI es un **producto funcional con una base de seguridad correcta** para un MVP, y con
**v2.1.0 el grueso del hardening a nivel de código (OWASP) ya está aplicado y verificado**. Para
ofrecerlo a clientes con confianza, lo que **resta** es cerrar el **despliegue seguro** (HTTPS +
rotación de secretos, lo que queda de Fase A) y la **Fase B** (base de datos, cuentas, privacidad)
— estimado en **≈ 2-3 semanas**, menos que antes porque buena parte de Fase A ya está hecha. La
**Fase C** queda como proceso continuo si se busca certificación formal (SOC 2 / ISO). Sí:
**estamos implementando las mejores prácticas (OWASP) para dejarlo seguro y listo para auditoría.**
