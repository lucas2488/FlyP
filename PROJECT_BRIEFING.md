# FlyP — Briefing Completo del Proyecto

> Este documento explica el estado actual del proyecto para que cualquier instancia de Claude pueda entender el contexto completo sin necesidad de acceso al historial de conversaciones o a Planka.

---

## ¿Qué es FlyP?

**FlyPromociones** es una app Android de búsqueda de vuelos para el mercado argentino. Actúa como agregador — muestra resultados de Skyscanner, Despegar, Almundo, etc. — y monetiza a través de **affiliate links de Impact.com**.

El backend fue construido para reemplazar un servidor n8n que se cayó y perdió datos. Hoy hace mucho más que eso.

---

## Stack completo

### Backend (`FlyBackend`) — Python/FastAPI
- **Repo GitHub:** `github.com/lucas2488/FlyP`
- **Servidor:** VPS Ubuntu ARM64 — `178.105.195.245`
- **Docker:** FastAPI + PostgreSQL 16 + n8n + Nginx + Certbot
- **Dominio API:** `https://api.flypromociones.com`

### Dashboard de Marketing (`FlyMarketingDashboard`) — React/Vite/TS
- **Repo GitHub:** `github.com/lucas2488/FlyMarketingDashboard`
- **Deploy:** Firebase Hosting — `https://admob-app-id-2456247859.web.app`
- **API Key dashboard:** `29d06217db3e21c5ad62dc76ac6ec6914f9a67ced22c6add655335ce8a29407b`

### App Android (`FlyAndroid`)
- Conecta a `https://api.flypromociones.com/` via Retrofit
- Firebase Cloud Messaging (FCM) para recibir push notifications

---

## Arquitectura del backend

```
app/
├── main.py              # FastAPI app, lifespan, CORS, APScheduler (30 min)
├── config.py            # Settings con pydantic-settings
├── database.py          # Engine async + sessionmaker
├── models.py            # 9 tablas SQLAlchemy
├── schemas.py           # Pydantic schemas
└── routers/
│   ├── profile.py       # POST /api/v1/profile
│   ├── impact.py        # POST /api/v1/impact
│   ├── events.py        # POST /api/v1/events/*
│   └── analytics.py     # GET /api/v1/analytics/* (con X-API-Key auth)
└── services/
    ├── firebase_service.py         # Wrapper FCM
    ├── notification_engine.py      # Evalúa caídas de precio
    └── notification_dispatcher.py  # APScheduler job (envía FCM)
```

---

## Endpoints de la API

### Reciben datos del Android
| Método | Endpoint | Descripción |
|---|---|---|
| GET | `/api/v1/health` | Liveness check |
| POST | `/api/v1/profile` | Upsert de perfil de usuario (se llama en cada app open) |
| POST | `/api/v1/impact?url=...` | Registra click en affiliate link |
| POST | `/api/v1/events/price-snapshot` | Precios del calendario de fechas (CalendarPricesViewModel) |
| POST | `/api/v1/events/search-result` | Resultado de búsqueda de vuelos |
| POST | `/api/v1/events/flight-selected` | Usuario tocó un vuelo específico |

### Dashboard de analytics (auth por header `X-API-Key`)
| Método | Endpoint | Descripción |
|---|---|---|
| GET | `/api/v1/analytics/overview` | KPIs generales |
| GET | `/api/v1/analytics/top-routes` | Rutas más buscadas |
| GET | `/api/v1/analytics/notifications` | Métricas push |
| GET | `/api/v1/analytics/revenue` | Clicks afiliados |
| GET | `/api/v1/analytics/price-watches` | Rutas más watcheadas |

---

## Base de datos — estado actual (2026-05-24)

| Tabla | Registros | Descripción |
|---|---|---|
| `user_profiles` | **2.875** | Un registro por usuario Android |
| `price_watches` | **1.977 activos** | Rutas monitoreadas por usuario |
| `airport_cache` | **106** | Aeropuertos vistos en búsquedas |
| `impact_link_log` | **146** | Clicks en affiliate links |
| `price_snapshots` | 2 | Precios del calendario (recién empezando) |
| `search_events` | 0 | Eventos de búsqueda (por integrar en Android) |
| `notification_queue` | 0 | Cola de notificaciones push |
| `notification_log` | 0 | Log de notificaciones enviadas |
| `price_history` | — | Historial de precios por ruta |

---

## Sistema de notificaciones push — cómo funciona

**Este es el core del producto.** NO usa la API de Skyscanner para monitorear precios. En su lugar, usa datos de los propios usuarios:

1. El usuario abre el calendario de fechas en la app Android
2. `CalendarPricesViewModel` recibe los precios del mes desde Skyscanner
3. Android envía esos datos a `POST /events/price-snapshot`
4. El backend guarda los precios "bajos" y los compara con la última referencia del usuario para esa ruta
5. Si el precio bajó ≥ 15% respecto a la última vez que ese usuario buscó → encola notificación en `notification_queue`
6. APScheduler corre cada 30 min → `notification_dispatcher.py` → envía FCM a los tokens registrados
7. La notificación llega al Android con origen, destino, precio y porcentaje de baja

**Condiciones para encolar una notificación:**
- Bajada ≥ 15% (configurable: `PRICE_DROP_THRESHOLD`)
- Usuario tiene FCM token registrado
- Cooldown de 48h por usuario/ruta (configurable: `NOTIFICATION_COOLDOWN_HOURS`)
- Máximo 2 notificaciones por usuario por día (configurable: `MAX_NOTIFICATIONS_PER_USER_PER_DAY`)
- No hay ya una notificación `pending` para ese usuario/ruta

**Firebase Admin SDK:** Ya inicializado. El `firebase-service-account.json` está montado en el contenedor.

---

## Variables de entorno del servidor

```
# PostgreSQL
DB_USER / DB_PASSWORD / DB_NAME

# API
API_SECRET_KEY
ANALYTICS_API_KEY=29d06217db3e21c5ad62dc76ac6ec6914f9a67ced22c6add655335ce8a29407b
CORS_ORIGINS=*

# Notificaciones
NOTIFICATION_COOLDOWN_HOURS=48
MAX_NOTIFICATIONS_PER_USER_PER_DAY=2

# Firebase
FIREBASE_CREDENTIALS_PATH=/app/firebase-service-account.json

# n8n (workflows internos)
N8N_BASIC_AUTH_USER / N8N_BASIC_AUTH_PASSWORD
N8N_DOMAIN=n8n.cordevs.com
API_DOMAIN=api.flypromociones.com

# SMTP (para emails de n8n)
SMTP_HOST/PORT/USER/PASS/SENDER
```

---

## Cómo deployar cambios al servidor

El servidor **no tiene git** — los archivos se copian con `scp`:

```bash
# Copiar archivos modificados
scp app/routers/nuevo.py root@178.105.195.245:/root/FlyP/app/routers/

# Rebuildar y reiniciar la API
ssh root@178.105.195.245 "
  cd /root/FlyP
  docker compose -f docker-compose.yml build api
  docker compose -f docker-compose.yml up -d api
"

# Ver logs en vivo
ssh root@178.105.195.245 "docker compose -f /root/FlyP/docker-compose.yml logs -f api"
```

---

## Estado de las tareas (Planka — autohosteado, no accesible externamente)

### ✅ Completado recientemente

**📈 Analytics API & Dashboard** — todo completado:
- Backend: router analytics con auth X-API-Key
- Backend: 5 endpoints (overview, top-routes, notifications, revenue, price-watches)
- Frontend: React + Vite + TS + Firebase Hosting
- Frontend: Auth guard + Layout + 5 páginas (Overview, Top Rutas, Notificaciones, Revenue, Price Watches)

**Motor de notificaciones** (commiteado, ya en servidor):
- `events.py`: 3 endpoints (price-snapshot, search-result, flight-selected)
- `notification_engine.py`: evaluación de caída de precios con cooldown
- `notification_dispatcher.py`: job APScheduler
- `firebase_service.py`: wrapper FCM
- Modelos: PriceSnapshot, SearchEvent, NotificationQueue, AirportCache

---

### ❌ Pendiente — por prioridad

#### 🔴 Alta prioridad (P0)

**📊 Data & Enriquecimiento:**
- **Activar Alembic** — hoy las migraciones son manuales con `ALTER TABLE IF NOT EXISTS`. Antes de agregar más tablas, hay que tener migraciones versionadas
- **Nueva tabla `user_search_history`** — hoy solo se guarda la última búsqueda. Para campañas personalizadas se necesita el historial
- **Backend: aceptar `userSegment` + `engagementScore` en `/profile`** — el Android los calcula localmente pero no los manda al backend
- **Android: enviar esos campos** — cambio en `UserBehaviorRepository.kt` y `UserProfileData.kt`
- **Android: enviar historial de búsquedas** (`recentSearches` de Room)

**🤖 Automatización:**
- (Firebase: ✅ ya funciona)
- (APScheduler: ✅ ya funciona)

#### 🟠 Media prioridad (P1)

**📊 Data:**
- Fix bug: `lastFlightStatusClickDate` hardcodeado null en Android (`UserBehaviorRepository.kt`)
- `segmentation.py` en backend — calcular segmentos (heavy_searcher, casual, etc.) desde los datos
- `POST /api/v1/profile/favorites` — endpoint para sincronizar favoritos desde `FavoriteSSDao`
- Android: `syncFavorites()` una vez que exista el endpoint

**🤖 Automatización:**
- Ampliar `notification_log` con `opened_at` (para trackear si el usuario abrió la notif)
- `POST /api/v1/notifications/opened` — Android avisa cuando abre una push
- Cron de re-engagement para usuarios inactivos > 30 días
- Android: tracking de apertura de notificaciones

**📈 Dashboard:**
- `GET /analytics/retention` — cohort de retención de usuarios
- Documentación API para el especialista de marketing

#### 🟡 Baja prioridad (P2)

**🎯 Campañas & Segmentación** — sistema completo a construir:
- Tablas: `user_segments_snapshot`, `campaigns`, `campaign_sends`
- `campaign_engine.py` — envío masivo segmentado
- Router admin de campañas (CRUD + `/send` + `/stats`)
- Webhook para n8n
- Campaña automática de bienvenida

**✨ App Android — Features:**
- Filtros de resultados (la más importante para competir)
- Historial de precios con gráfico
- Modo oscuro
- Widget de homescreen
- Vista mapa de destinos por precio
- Perfil de usuario y viajes guardados

**⚡ App Android — Quick wins:**
- Skeleton loading (en lugar de spinner genérico)
- Pull to refresh en resultados
- Logos de aerolíneas en tarjetas
- Haptic feedback en acciones clave
- Fix: `startUserSession()` nunca se llama en MainActivity
- Retry automático en errores de red durante polling
- Mejorar empty state de búsqueda sin resultados

---

## Próximos pasos recomendados

1. **Alembic** — antes de tocar más la DB, tener migraciones versionadas
2. **Campos de segmentación** — alimentar la DB con datos más ricos del Android
3. **`notification_log` con `opened_at`** — cerrar el loop de analytics de notificaciones
4. **Filtros de resultados en Android** — la feature más pedida para competir con Skyscanner/Kayak
