# Fly Promociones — Backend

## Qué es este sistema

Backend + Dashboard de analítica para la app Android **Fly Promociones** (tipo Skyscanner para Argentina).
La app monitorea precios de vuelos y envía push notifications cuando bajan.
Este repo es el servidor que recibe datos del app, los persiste, evalúa bajadas de precio y dispara notificaciones FCM.

El dashboard (`FlyMarketingDashboard`, repo hermano) consume este backend y da visibilidad de métricas, campañas y templates.

**Producción:** VPS Ubuntu ARM64 en `178.105.195.245`
**App Android:** `api.flypromociones.com` → este backend
**Dashboard:** Firebase Hosting → consume `api.flypromociones.com/api/v1/analytics/*`

---

## Stack

| Componente | Tecnología | Puerto |
|---|---|---|
| API | FastAPI + uvicorn (Python 3.12) | 8000 |
| Base de datos | PostgreSQL 16 | 5432 |
| Reverse proxy | Nginx 1.27 | 80/443 |
| Push notifications | Firebase Admin SDK (FCM) | — |
| Scheduler | APScheduler (AsyncIOScheduler) | — |
| Workflows internos | n8n self-hosted | 5678 |
| SSL | Certbot / Let's Encrypt | — |

---

## Modelo de identidad — CRÍTICO

**No existen cuentas de usuario.** El único identificador del dispositivo es el **FCM token**.

```
fcm_token  →  identidad real del dispositivo (NOT NULL, UNIQUE en user_profiles)
user_id    →  columna legacy, siempre NULL, reservada para autenticación futura
```

Reglas absolutas:
- Nunca crear lógica que dependa de `user_id` — siempre es NULL
- Todo lookup de `UserProfile` se hace por `UserProfile.fcm_token`
- En tablas hijas (`price_watches`, `notification_queue`, etc.), el campo `user_id` almacena el valor del FCM token del dispositivo (es la FK lógica hacia user_profiles.fcm_token)
- Nunca hay `anonymous_XXXX` — si los ves en la DB es basura legacy

---

## Estructura del proyecto

```
app/
├── main.py                  # FastAPI app, lifespan, APScheduler jobs
├── config.py                # Settings via pydantic-settings
├── database.py              # Engine async + sessionmaker
├── models.py                # SQLAlchemy models (15 tablas)
├── schemas.py               # Pydantic schemas
└── routers/
│   ├── profile.py           # POST /api/v1/profile
│   ├── impact.py            # POST /api/v1/impact
│   ├── events.py            # POST /api/v1/events/price-calendar, month-calendar
│   ├── favorites.py         # POST /api/v1/favorites
│   ├── notifications.py     # POST /api/v1/notifications/{id}/opened
│   ├── analytics.py         # GET  /api/v1/analytics/* (dashboard)
│   ├── admin.py             # GET/POST /api/v1/admin/* (templates, campañas)
│   ├── campaigns.py         # campañas masivas
│   └── internal.py          # endpoints internos
└── services/
    ├── notification_engine.py     # evalúa bajadas, encola notificaciones
    ├── notification_dispatcher.py # APScheduler job: envía pending → FCM
    ├── welcome_service.py         # notificación de bienvenida con 24h delay
    ├── reengagement_service.py    # push post-búsqueda (abandono)
    ├── campaign_engine.py         # ejecuta campañas masivas
    ├── campaign_scheduler.py      # APScheduler job: dispara campañas del calendario
    ├── campaign_generator.py      # genera drafts con IA (OpenAI)
    ├── segment_service.py         # recalcula segmentos de usuario (nightly)
    ├── firebase_service.py        # wrapper FCM (send_notification, send_to_topic)
    ├── airport_resolver.py        # resuelve IATA → nombre legible
    └── cleanup_service.py         # purga notification_queue > 7d, log > 90d
```

---

## APScheduler jobs

| Job | Intervalo | Descripción |
|---|---|---|
| `process_notification_queue` | cada 30 min | envía pending price-drops al FCM |
| `process_reengagement_queue` | cada 5 min | re-engagement post-búsqueda |
| `check_scheduled_campaigns` | cada 1h | dispara campañas del calendario |
| `process_welcome_notifications` | cada 1h | envía welcomes con 24h delay |
| `recalculate_segments` | diario 3am AR | recalcula heavy/casual/inactive |
| `purge_old_queue_items` | diario 4am AR | limpieza: queue 7d, log 90d |

---

## Endpoints principales

| Método | Path | Origen | Descripción |
|---|---|---|---|
| GET | `/api/v1/health` | cualquiera | liveness check |
| POST | `/api/v1/profile` | app Android | upsert de dispositivo (por fcm_token) |
| POST | `/api/v1/impact` | app Android | registra click en affiliate link |
| POST | `/api/v1/events/price-calendar` | app Android | precios del mes por ruta, dispara notifs |
| POST | `/api/v1/events/month-calendar` | app Android | tendencia de precio mensual |
| POST | `/api/v1/favorites` | app Android | sync de rutas favoritas |
| POST | `/api/v1/notifications/{id}/opened` | app Android | tracking de apertura |
| GET | `/api/v1/analytics/*` | dashboard | métricas agregadas (API key) |
| GET/POST | `/api/v1/admin/*` | dashboard | templates, campañas (API key) |

---

## Pipeline de notificaciones

```
app Android
  → POST /events/price-calendar (precios del mes para una ruta)
      → notification_engine.evaluate_price_drop()
          → chequea cooldown por nivel (soft/strong/urgent) en price_watches
          → chequea pending existente para ese token+ruta
          → encola en notification_queue (status=pending)

APScheduler cada 30min
  → notification_dispatcher.process_notification_queue()
      → busca UserProfile por fcm_token (item.user_id = fcm_token)
      → elige template aleatorio según país y drop_level
      → llama firebase_service.send_notification()
      → marca status=sent, actualiza cooldown en price_watches
      → registra en notification_log
```

Niveles de bajada y cooldowns (configurables via env vars):
- `urgent`: ≥15% → cooldown 24h
- `strong`: ≥10% → cooldown 24h
- `soft`:   ≥5%  → cooldown 48h

---

## Base de datos — tablas clave

| Tabla | PK | Descripción |
|---|---|---|
| `user_profiles` | `id` (SERIAL) | Un perfil por dispositivo. `fcm_token` UNIQUE NOT NULL. `user_id` siempre NULL. |
| `price_watches` | `id` | Rutas a monitorear. `user_id` almacena el fcm_token del dispositivo. |
| `notification_queue` | `id` | Cola de notificaciones. `user_id` almacena fcm_token. |
| `notification_log` | `id` | Audit trail de envíos. Retención 90 días. |
| `notification_templates` | `id` | Templates por país y nivel. Editables desde el dashboard. |
| `campaigns` | `id` | Campañas masivas (draft→sending→sent). |
| `campaign_sends` | `id` | Registro individual por campaña+dispositivo. |
| `campaign_calendar` | `id` | Slots semanales (Lun/Mié/Vie 10am AR) y fechas especiales. |
| `search_events` | `id` | Eventos de búsqueda del app. `user_id` almacena fcm_token. |
| `flyp_user_favorites` | `id` | Rutas favoritas. Tiene columna `fcm_token` directamente. |
| `price_history` | `id` | Histórico de precios mínimos por ruta + moneda. |
| `price_snapshots` | `id` | Snapshots de precios bajos por día. |
| `price_months` | `id` | Tendencia mensual de precios. |
| `airport_cache` | `iata_code` | Cache de aeropuertos vistos. |

---

## Campañas

Dos tipos:
- **Semanales** (Lun/Mié/Vie 10am AR): broadcast por FCM topic `flypromociones_AR`. Una sola llamada a Firebase entrega a todos los suscriptores.
- **Especiales** (feriados, fechas comerciales): per-user con ruta personalizada según historial de búsqueda.

El `campaign_scheduler` corre cada hora, detecta si es momento de disparar un slot, crea la Campaign y llama a `execute_campaign()`. Si la campaña ya existe como `draft` (pre-seeded desde el dashboard), también la ejecuta — no la saltea.

---

## Deploy

```bash
# Conectarse al servidor
ssh root@178.105.195.245
cd /root/FlyP

# Copiar archivos locales al servidor (desde la máquina dev)
scp app/routers/profile.py root@178.105.195.245:/root/FlyP/app/routers/profile.py
# ... (o rsync para múltiples archivos)

# Rebuild y restart
docker compose -f docker-compose.yml build api
docker compose -f docker-compose.yml up -d api

# Ver logs
docker compose -f docker-compose.yml logs api -f

# Las migraciones Alembic corren automáticamente en el startup (CMD del Dockerfile)
```

El `.env` vive solo en el servidor (`/root/FlyP/.env`), nunca en git.

---

## Convenciones de migraciones

- Numeradas secuencialmente: `0001_`, `0002_`, ...
- Siguiente disponible: verificar con `ls alembic/versions/`
- Cada migración es **no reversible** si modifica datos (downgrade = pass)
- Nunca modificar una migración que ya corrió en producción — crear una nueva

---

## Variables de entorno importantes

```bash
DATABASE_URL                         # PostgreSQL async
FIREBASE_CREDENTIALS_PATH            # path al service account JSON
NOTIFICATION_COOLDOWN_URGENT_HOURS   # default 24
NOTIFICATION_COOLDOWN_STRONG_HOURS   # default 24
NOTIFICATION_COOLDOWN_SOFT_HOURS     # default 48
MAX_NOTIFICATIONS_PER_USER_PER_DAY   # default 2
ANALYTICS_API_KEY                    # autenticación del dashboard
OPENAI_API_KEY                       # generación de templates con IA
```

---

## Relación con el app Android

El app Android (`FlyAndroid`) conecta a este backend desde `NetworkModule.kt`:
```kotlin
.baseUrl("https://api.flypromociones.com/")
```

Llama a:
- `POST /api/v1/profile` — en cada apertura, búsqueda, y update de FCM token
- `POST /api/v1/events/price-calendar` — cuando fetchea precios del mes para una ruta
- `POST /api/v1/favorites` — al sincronizar favoritos
- `POST /api/v1/impact` — cuando el usuario abre un affiliate link

---

## Decisiones de arquitectura

**FCM token como única identidad**: No existen cuentas de usuario. El token es la clave. `user_id` existe en el schema como columna nullable reservada para cuando haya autenticación real (futuro).

**notification_queue como buffer**: Las notificaciones no se envían instantáneamente. Se encolan y el dispatcher corre cada 30 min. Esto permite rate limiting, deduplicación y retry sin complejidad extra.

**Cooldowns por nivel independientes**: urgent/strong/soft tienen sus propios timestamps en price_watches. La lógica es: si ya notificaste urgent, soft todavía puede disparar pasado su cooldown. Esto da granularidad pero puede parecer duplicado para el usuario — ajustar cooldowns si se vuelve molesto.

**commit-after-all en dispatcher**: El dispatcher procesa el batch entero y hace un solo commit. Si el servidor se cae entre el envío FCM y el commit, el ítem queda pending y se re-intenta. Aceptable dado el volumen actual.

**Campañas semanales por FCM topic**: Un solo send a Firebase llega a todos los suscriptores. Requiere que el app esté suscripto al topic `flypromociones_AR`.
