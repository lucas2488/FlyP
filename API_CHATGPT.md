# API de Marketing — Fly Promociones (para ChatGPT)

Documentación de la API que usa ChatGPT para gestionar campañas y consultar métricas,
**sin tener que entrar al dashboard web**.

- **Base URL:** `https://api.flypromociones.com/api/v1`
- **Auth:** header `X-API-Key: <TU_API_KEY>` en cada request.
- **Schema OpenAPI** (para importar como Action en un GPT): `openapi_chatgpt.yaml`

> La API key dedicada de ChatGPT se entrega aparte (no se guarda en este repo).
> Es distinta de la del dashboard y se puede revocar sola (borrando `CHATGPT_API_KEY` del `.env`).

---

## Conectar a un GPT personalizado (Custom GPT Action)

1. En ChatGPT: crear/editar un GPT → **Configure** → **Actions** → **Create new action**.
2. En **Schema**, pegar el contenido de `openapi_chatgpt.yaml` (o importarlo).
3. En **Authentication** elegir **API Key** → **Auth Type: Custom** → Header name `X-API-Key` → pegar la key.
4. Guardar. Ya puede llamar la API sola.

---

## Reglas importantes de campañas

- **Broadcast a TODOS:** poné `target_topic = "flypromociones_AR"`. En ese caso el `segment` se ignora.
- **Solo a un segmento (per-user):** poné `segment` y dejá `target_topic` vacío.
- `scheduled_at` va en **hora Argentina**, ISO sin zona: `"2026-09-25T10:00:00"`. El scheduler la
  dispara sola a esa fecha/hora. No hace falta enviarla a mano.
- Al crear, la campaña queda en **`draft`**. Solo se pueden **editar/borrar** campañas `draft` o
  `scheduled`; las `sent` no (devuelve 409).
- **Segmentos:** `heavy_searcher`, `casual`, `inactive`, `new_user`.
- **Estados:** `draft`, `scheduled`, `sending`, `sent`, `failed`.

---

## Endpoints

### Métricas (lectura)

| Método | Path | Query | Devuelve |
|---|---|---|---|
| GET | `/analytics/overview` | — | KPIs: usuarios, búsquedas, watches, clicks, notifs |
| GET | `/analytics/notifications` | `days=30` | total_sent, failed, delivery_rate, open_rate, by_type, by_day |
| GET | `/analytics/notifications/history` | `days=7, type, limit=100, offset=0` | envíos individuales (paginado) |
| GET | `/analytics/top-routes` | `limit=20` | rutas más buscadas |
| GET | `/analytics/route-prices` | `origin*, destination*, currency, days=30` | calendario de precios por fecha de viaje |
| GET | `/admin/campaigns/segment-counts` | — | cantidad de usuarios por segmento |

\* requerido

### Campañas (gestión)

| Método | Path | Notas |
|---|---|---|
| GET | `/admin/campaigns` | filtros: `status, segment, since, until, limit` |
| GET | `/admin/campaigns/{id}` | ver una campaña |
| POST | `/admin/campaigns` | crear (queda draft) — body abajo |
| PATCH | `/admin/campaigns/{id}` | editar (solo draft/scheduled) |
| DELETE | `/admin/campaigns/{id}` | borrar (solo draft) |
| POST | `/admin/campaigns/{id}/send` | enviar YA (no espera el scheduled_at) |
| GET | `/admin/campaigns/{id}/stats` | resultado: sent/opened/failed/skipped |

### Body de crear/editar campaña

```json
{
  "name": "Jujuy — todos 25/09",
  "target_topic": "flypromociones_AR",
  "custom_title": "🌈 Jujuy desde $50.000",
  "custom_body": "De Buenos Aires a Jujuy desde $50.000 ✈️ Buscalo en Fly Promociones.",
  "scheduled_at": "2026-09-25T10:00:00"
}
```

Campos opcionales para envío **per-user** (en vez de topic): `segment`, `campaign_type`
(`route`|`top_auto`|`category`), `route_origin`, `route_destination`, `category_tag`.

---

## Ejemplos (curl)

```bash
KEY="<TU_API_KEY>"
BASE="https://api.flypromociones.com/api/v1"

# KPIs
curl -H "X-API-Key: $KEY" "$BASE/analytics/overview"

# Precios de una ruta en ARS
curl -H "X-API-Key: $KEY" "$BASE/analytics/route-prices?origin=BUEA&destination=JUJ&currency=ARS"

# Listar campañas programadas de la semana
curl -H "X-API-Key: $KEY" "$BASE/admin/campaigns?status=scheduled&since=2026-09-21&until=2026-09-27"

# Crear una campaña broadcast programada
curl -X POST -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"name":"Jujuy 25/09","target_topic":"flypromociones_AR","custom_title":"🌈 Jujuy desde $50.000","custom_body":"De Buenos Aires a Jujuy desde $50.000 ✈️","scheduled_at":"2026-09-25T10:00:00"}' \
  "$BASE/admin/campaigns"

# Editar la fecha de la campaña 93
curl -X PATCH -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"scheduled_at":"2026-09-26T10:00:00"}' "$BASE/admin/campaigns/93"

# Borrar un borrador
curl -X DELETE -H "X-API-Key: $KEY" "$BASE/admin/campaigns/99"
```
