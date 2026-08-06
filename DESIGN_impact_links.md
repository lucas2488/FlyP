# Diseño — Cache/creación de links de Impact.com en el backend

> Estado: **diseño aprobado**, pendiente de implementación.
> Alcance de este doc: **solo backend** (FlyBackend / `api.flypromociones.com`).
> La implementación en la app Android se define después.

## Problema

Hoy, cuando el usuario toca una opción de compra, la app (`RedirectViewModel.retrieveUrl`):

1. Espera 3 segundos (`delay(3000)`).
2. Crea el tracking link de Impact **on-demand, en el cliente** (`SSApiService.createTrackingLink`, `Type=Regular`).
3. Redirige **inmediatamente** al link recién creado.

Sospecha: el redirect dispara tan rápido después de crear el link que el link de
Impact todavía no está "listo" → la redirección no funciona de forma confiable.

Además, las credenciales Basic-auth de Impact están **hardcodeadas en el APK**
(extraíbles descompilando la app) — riesgo de seguridad.

## Objetivo

Desacoplar **creación** de **uso**:

- **Pre-crear** los links al entrar al detalle del vuelo (batch de ~10), cacheados
  en la base. Se "calientan" mientras el usuario mira la pantalla.
- Al **click**, la API devuelve el link ya creado (instantáneo). Si no lo tiene,
  lo crea al momento (fallback), y si tampoco puede, la app usa su fallback propio.

Beneficio extra: las credenciales de Impact salen del cliente y pasan al server.

## Flujo nuevo

```
1. Usuario abre el DETALLE del vuelo (N opciones)
   app → POST /impact/prewarm { user_id, urls:[...] }   (fire-and-forget)
   server: por cada url no cacheada → crea link de Impact → guarda

2. Usuario lee la pantalla (los links se calientan)

3. Usuario toca una opción
   app → POST /impact/resolve { url }
   server: busca en cache → devuelve { impact_url } (o crea si falta)
   app: navega a impact_url  (reusa RedirectLoadingScreen / _redirectUrl)

   Si el server devuelve null o falla la llamada:
   app → fallback existente (línea 87: Skyscanner redirect API) → línea 104 (directo)
```

## Decisiones lockeadas

| # | Decisión | Valor |
|---|----------|-------|
| 1 | Tipo de link | **Regular** siempre (el usuario nunca ve el link) |
| 2 | Atribución | **Cache global genérico** — sin SubId por usuario |
| 3 | Vencimiento | **No vencen** → cache eterno, sin TTL ni refresh |
| 4 | Clave de dedup | **SHA-256 de la url** (`char(64)` unique); url completa en `text` |
| 5 | Contrato del click | **Opción B**: server devuelve JSON `{ impact_url }`, la app navega |
| 6 | Fallback | En la **app** (server solo hace Impact; devuelve null si no puede) |
| 7 | Escapeo | El **server** replica `encodeQueryParamValues` antes de llamar a Impact |

## Modelo de datos — tabla `impact_links`

| columna | tipo | notas |
|---|---|---|
| `id` | int PK | |
| `url_hash` | `char(64)` **unique** | SHA-256 de la url cruda (clave de dedup) |
| `url` | `text` | url original completa (larga, variable) |
| `impact_url` | `text` nullable | TrackingURL de Impact ya creada |
| `status` | `varchar(10)` | `pending` / `ready` / `failed` |
| `created_at` | `timestamptz` | default now() |
| `last_used_at` | `timestamptz` nullable | última vez que se resolvió a un click |
| `use_count` | `int` default 0 | cuántos clicks sirvió (métrica) |

Migración Alembic nueva (`00XX_impact_links.py`).

**Por qué hash y no `unique` sobre la url:** el índice btree único de Postgres tiene
un límite de ~2.700 bytes por entrada; un deeplink largo lo rompe. El SHA-256 es
siempre 64 chars fijos sin importar el largo de la url. Mismo input → mismo hash.

El **log de clicks** sigue en `impact_link_log` (lo escribe `/resolve`, no `/prewarm`).
No se toca — es lo que alimenta la página Revenue del dashboard.

## Endpoints

### `POST /api/v1/impact/prewarm`  (pre-crear en batch)

```json
// request
{ "user_id": "fcm...", "urls": ["/transport/...", "/transport/...", ...] }

// response
{ "results": [ { "url": "/transport/...", "status": "ready", "impact_url": "..." }, ... ] }
```

- Por cada url: `INSERT ... ON CONFLICT (url_hash) DO NOTHING` (idempotente — evita
  crear dos veces el mismo link ante requests concurrentes).
- A las nuevas (`pending`) les crea el link de Impact **en paralelo** (concurrencia
  acotada) → `ready` con la `impact_url`, o `failed` si Impact devuelve error.
- La app lo llama **fire-and-forget** (no espera la respuesta).

### `POST /api/v1/impact/resolve`  (click → devuelve la url)

```json
// request
{ "url": "/transport/...", "user_id": "fcm..." }

// response OK
{ "impact_url": "https://..." }
// response sin link (la app hace fallback)
{ "impact_url": null }
```

- Busca por `url_hash`:
  - `ready` → devuelve `impact_url`, loguea click en `impact_link_log`, bump
    `use_count` / `last_used_at`.
  - `pending` / `failed` / inexistente → intenta **crear al momento**
    (idempotente). Si sale → guarda y devuelve. Si falla → `{ impact_url: null }`.
- **Nunca** hace el fallback de Skyscanner — eso es responsabilidad de la app.

## Creación del link de Impact (server-side)

Replica lo que hoy hace la app:

1. `encoded = encodeQueryParamValues(url)`  (portar la lógica de escapeo de valores
   de query params — sin esto Impact no decodifica bien).
2. `deepLink = "https://www.skyscanner.net" + encoded`.
3. `POST https://api.impact.com/Mediapartners/{SID}/Programs/13416/TrackingLinks`
   `?DeepLink={deepLink}&Type=Regular`, con header `Authorization: Basic <token>`.
4. Respuesta → `TrackingURL` = `impact_url`.

Credenciales (`SID`, token Basic-auth) → **al `.env` del backend**
(`IMPACT_ACCOUNT_SID`, `IMPACT_AUTH_TOKEN`). Fuera del cliente.

## Concurrencia / idempotencia

- `prewarm` y `resolve` pueden pedir la misma url a la vez → el `unique(url_hash)` +
  `ON CONFLICT DO NOTHING` garantiza una sola fila; solo el que la insertó llama a
  Impact. (Mismo criterio que usamos para arreglar el deadlock de `airport_cache`.)

## Analytics / dashboard (a futuro, opcional)

Con la tabla nueva se puede exponer en el dashboard: links cacheados totales,
creados por día, hit-rate del cache, top urls por `use_count`. Los **clicks** ya
salen de `impact_link_log` (página Revenue actual). No es parte del MVP.

## Seguridad

Mover la creación al server saca el token Basic-auth de Impact del APK. Después de
migrar, **rotar** ese token en Impact (el actual ya está expuesto en la app publicada).

## Pendiente (fuera de este doc — implementación app)

- Portar `encodeQueryParamValues` al server (o confirmar formato exacto de la url).
- App: llamar `prewarm` al abrir el detalle; `resolve` al click; mantener el
  fallback de línea 87/104 cuando `resolve` devuelve null o falla.
- Definir si `prewarm` manda las N urls del detalle o solo las visibles.
