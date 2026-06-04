# StillAI — Arquitectura, funcionamiento y seguridad

> Documento para revisión técnica/de negocio. Estado: **prototipo funcional avanzado (MVP+)**.
> Apto para demos y uso interno; requiere una **fase de endurecimiento** antes de oferta comercial.

---

## 1. Resumen ejecutivo

StillAI genera **bodegones de producto** (composiciones con fondo transparente) de forma
automática: el usuario sube imágenes, la IA decide el orden y el tamaño real de cada producto,
y el sistema entrega un PNG listo para tienda, catálogo o redes. Incluye un módulo **por lotes**
(carpetas → muchos bodegones) y **login** de usuario.

- **Madurez:** prototipo funcional, una sola instancia, almacenamiento local.
- **Seguridad:** base correcta (contraseñas hasheadas, sesiones firmadas, rutas protegidas),
  pero **faltan controles de producción** (HTTPS, límites de abuso, gestión de secretos, etc.).
- **Para ofrecerlo a clientes:** se necesita una fase de *hardening* (estimada abajo) y decisiones
  sobre privacidad de datos, porque las imágenes se procesan con proveedores de IA externos.

---

## 2. Stack tecnológico

| Capa | Tecnología |
|---|---|
| Backend / API | **Python 3.14 + FastAPI + Uvicorn** (ASGI) |
| Sesiones / auth | Starlette **SessionMiddleware** (cookie firmada, `itsdangerous`) + **PBKDF2-HMAC-SHA256** |
| Procesamiento de imagen | **Pillow** (composición, recorte, sombras) · **PhotoshopAPI** (PSD/PSB, módulo legado) |
| IA (orden + tamaños) | Configurable: **Groq** (Llama 4 Scout), **Google Gemini** (2.5 Flash), **Anthropic Claude** |
| Frontend | **HTML + Vanilla JS + Tailwind CSS** (sin framework) · render con `<canvas>` |
| Almacenamiento | **Sistema de archivos local** (`sessions/`, `users.json`) — sin base de datos aún |
| Empaquetado | ZIP nativo (`zipfile`) para descargas por lote |

> No hay framework de frontend (React/Vue), ni base de datos, ni colas todavía — es intencional
> para el MVP. El roadmap (ver `ROADMAP_PLATAFORMA.md`) contempla DB, colas y nube para escalar.

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

### ⚠️ Lo que FALTA para producción (gaps)
| Gap | Riesgo | Prioridad |
|---|---|---|
| **Sin HTTPS/TLS** (corre en HTTP local) | credenciales viajan sin cifrar | 🔴 Crítico |
| **Cookie sin `Secure`** (`https_only=False`) | depende de no-HTTPS | 🔴 Crítico (al pasar a HTTPS) |
| **Sin rate limiting** | fuerza bruta de login, abuso de IA (costos) | 🔴 Alta |
| **Registro abierto** | cualquiera crea cuenta | 🟠 Media |
| **Sin límite de tamaño/cantidad de subidas** | DoS por archivos enormes/masivos | 🟠 Media |
| **CORS permisivo** (`allow_origins=["*"]`) | endurecer al dominio real | 🟠 Media |
| **Mensajes de error con detalle** | fuga de información interna | 🟢 Baja |
| **Política de contraseña débil** (mín. 4) | cuentas débiles | 🟢 Baja |
| **Sin verificación de email / reset / 2FA** | recuperación y robo de cuenta | 🟢 Baja |
| **Dependencias sin escaneo de vulnerabilidades** | CVEs en librerías | 🟠 Media |

### ✅ Hardening aplicado (esta versión — OWASP, "audit-ready")
Implementado a nivel de código, manteniendo la app 100 % funcional:
- **Rate limiting** anti fuerza-bruta en login/registro (por IP y por usuario) → `429` al exceder.
- **Headers de seguridad** en todas las respuestas: `Content-Security-Policy`, `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy`, `Permissions-Policy` (y `HSTS` cuando hay HTTPS).
- **Cookie de sesión `Secure` configurable** (`COOKIE_SECURE=true` en producción) + HSTS.
- **CORS restringido** a orígenes permitidos (ya no `*`), con credenciales.
- **Límites de subida**: tamaño por archivo (`MAX_FILE_MB`) y cantidad por grupo (`MAX_FILES`).
- **Política de contraseña** (mín. 8) y **registro configurable** (`REGISTRATION_OPEN=false` lo cierra).
- **Errores genéricos** en producción (no se filtran trazas internas; detalle solo con `DEBUG=true`).
- **Config por entorno** documentada en `.env.example` (sin secretos).

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

**Fase A — Seguridad base (1-2 semanas)**
1. HTTPS/TLS obligatorio + cookies `Secure`.
2. Rate limiting (login + endpoints de IA).
3. Límites de tamaño y cantidad de subidas.
4. CORS restringido al dominio real.
5. Rotación de secretos + gestor de secretos (no `.env` plano en prod).
6. Escaneo de dependencias (pip-audit) en CI.

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

StillAI es un **producto funcional con una base de seguridad correcta** para un MVP. Para
ofrecerlo a clientes con confianza, recomendamos ejecutar la **Fase A + B** del plan (≈ 3-4
semanas) antes del lanzamiento, y la **Fase C** como proceso continuo si se busca certificación
formal (SOC 2 / ISO). Sí: **podemos implementar las mejores prácticas (OWASP) para dejarlo
seguro y listo para auditoría.**
