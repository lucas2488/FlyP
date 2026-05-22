# FlyP Backend — CLAUDE.md

## Qué es este proyecto

Backend propio que reemplaza al servidor `n8n.cordevs.com` que se cayó y perdió todos los datos.
Recibe datos del app Android **FlyPromociones** y los persiste en PostgreSQL.
Corre en producción en un VPS Ubuntu 26.04 ARM64 (`178.105.195.245`).

---

## Stack

| Componente | Tecnología | Puerto interno |
|---|---|---|
| API Python | FastAPI + uvicorn | 8000 |
| Workflows/CRM | n8n (self-hosted) | 5678 |
| Base de datos | PostgreSQL 16 | 5432 |
| Reverse proxy | Nginx 1.27 | 80/443 |
| SSL | Certbot (Let's Encrypt) | — |

---

## Dominios en producción

| Dominio | Apunta a |
|---|---|
| `n8n.cordevs.com` | n8n (workflows internos) |
| `api.flypromociones.com` | Python API (app Android) |

Ambos apuntan al mismo servidor (`178.105.195.245`). Nginx diferencia por `server_name`.

El certificado SSL es un solo cert SAN que cubre los dos dominios, guardado en:
`/etc/letsencrypt/live/n8n.cordevs.com/`

---

## Endpoints de la API

| Método | Path | Descripción |
|---|---|---|
| GET | `/api/v1/health` | Liveness check |
| POST | `/api/v1/profile` | Guarda/actualiza perfil de usuario Android |
| POST | `/api/v1/impact?url=...` | Registra affiliate links abiertos |

### POST /api/v1/profile
Recibe `UserProfileData` (25 campos) desde `UserBehaviorRepository.kt` del app Android.
Si hay `lastSearchOriginIata` + `lastSearchDestinationIata`, crea automáticamente un `price_watch`.

### POST /api/v1/impact
Recibe `?url=...` como query param. Antes iba a `SSApiService.logURl()` hardcodeado a n8n.
Ahora usa `FPApiService.logUrl()` con base URL `api.flypromociones.com`.

---

## Base de datos — tablas

| Tabla | Descripción |
|---|---|
| `user_profiles` | Un registro por `user_id`. Se hace upsert en cada llamada al perfil. |
| `price_watches` | Rutas a monitorear por usuario (origin_iata → destination_iata). |
| `price_history` | Historial de precios por ruta (para detectar bajadas). |
| `notification_log` | Log de notificaciones FCM enviadas. |
| `impact_link_log` | Log de affiliate links abiertos. |

Las tablas se crean automáticamente al arrancar la API (SQLAlchemy `create_all` en el lifespan).

---

## Relación con el app Android

El app Android (`FlyAndroid`) conecta a este backend desde:

**`NetworkModule.kt`** — base URL del Retrofit compartido:
```kotlin
.baseUrl("https://api.flypromociones.com/")
```

**`FPApiService.kt`** — endpoints que llaman a este backend:
```kotlin
@POST("api/v1/profile")   // sendUserProfile()
@POST("api/v1/impact")    // logUrl()
```

**`UserBehaviorRepository.kt`** — llama a `sendUserProfile()` en:
- App open
- Flight search
- FCM token update

**`RedirectViewModel.kt`** — llama a `logUrl()` cuando el usuario abre un affiliate link.

### Versiones viejas del app
Las versiones anteriores tenían la URL de n8n hardcodeada:
`POST https://n8n.cordevs.com/webhook/profileAnalytics`
Esas llamadas ya no llegan a ningún lado. Para capturarlas habría que configurar
un workflow en n8n que reenvíe el webhook a `api.flypromociones.com/api/v1/profile`.

---

## Estructura del proyecto

```
FlyBackend/
├── app/
│   ├── main.py          # FastAPI app, lifespan, rutas
│   ├── config.py        # Settings con pydantic-settings (extra="ignore")
│   ├── database.py      # Engine async + sessionmaker
│   ├── models.py        # SQLAlchemy models (5 tablas)
│   ├── schemas.py       # Pydantic schemas (UserProfileData, WebhookResponse)
│   └── routers/
│       ├── profile.py   # POST /api/v1/profile
│       └── impact.py    # POST /api/v1/impact
├── nginx/
│   ├── templates/
│   │   └── default.conf.template  # Prod: HTTPS para ambos dominios
│   └── local.conf                 # Local: HTTP-only en localhost
├── scripts/
│   ├── init-ssl.sh      # Obtiene certs por primera vez (ya fue corrido)
│   ├── backup.sh        # Dump de PostgreSQL con fecha
│   └── restore.sh       # Restaura un backup con confirmación
├── docker-compose.yml           # Producción
├── docker-compose.override.yml  # Local (auto-aplicado)
├── Dockerfile
├── requirements.txt
└── .env.example
```

---

## Variables de entorno (.env en el servidor)

```
DB_USER / DB_PASSWORD / DB_NAME       → PostgreSQL
N8N_BASIC_AUTH_USER / PASSWORD        → Login de n8n
API_SECRET_KEY                        → Secret de la API Python
N8N_DOMAIN=n8n.cordevs.com
API_DOMAIN=api.flypromociones.com
SMTP_HOST/PORT/USER/PASS/SENDER       → Gmail para emails de n8n
CERTBOT_EMAIL=info@cordevs.com
```

El `.env` vive solo en el servidor (`/root/FlyP/.env`), nunca en git.

---

## Cómo conectarse al servidor

```bash
ssh root@178.105.195.245
cd /root/FlyP
```

## Comandos frecuentes en producción

```bash
# Ver estado
docker compose -f docker-compose.yml ps

# Ver logs en vivo
docker compose -f docker-compose.yml logs -f api
docker compose -f docker-compose.yml logs -f n8n

# Hacer backup
./scripts/backup.sh

# Actualizar la API tras cambios de código
# (copiar archivos + rebuild)
docker compose -f docker-compose.yml build api
docker compose -f docker-compose.yml up -d api
```

---

## Decisiones de arquitectura

- **Un solo cert SAN** cubre los dos dominios. El path del cert en nginx usa siempre
  `/etc/letsencrypt/live/n8n.cordevs.com/` para ambos server blocks.

- **`extra = "ignore"` en Settings** — necesario porque el `.env` tiene variables
  de n8n y SMTP que Pydantic no conoce y las rechaza sin este flag.

- **`resolver 127.0.0.11` en nginx** — Docker's internal DNS. Permite que nginx
  arranque aunque la API todavía no esté lista, resolviendo el nombre en runtime.

- **Certbot en modo standalone** — se usó para el primer cert porque nginx no podía
  arrancar sin los certs (chicken-and-egg). Para renovaciones usa modo webroot normal.

---

## Próximos pasos planificados

- [ ] Price monitoring: cron APScheduler cada 6h, consulta Skyscanner Month Calendar API
- [ ] Push notifications: Firebase Admin SDK para avisar bajadas de precio
- [ ] Workflow n8n: recibir `/webhook/profileAnalytics` y reenviar a la API (compat. apps viejas)
- [ ] Dashboard: Metabase o similar conectado a PostgreSQL para ver los datos
- [ ] Backup automático: crontab diario a las 3am
