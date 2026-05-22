# FlyP Backend

Backend propio de **FlyPromociones** — reemplaza el servidor n8n.cordevs.com que se cayó.

## Qué hace

Cada vez que un usuario abre la app Android o busca un vuelo, el app envía el perfil del usuario a este backend. El backend guarda esos datos en PostgreSQL y los usa para:

- **Guardar perfiles de usuarios**: token FCM, dispositivo, última búsqueda, cantidad de búsquedas, país, moneda seleccionada
- **Crear price watches automáticos**: cuando el usuario busca BUE → MIA, queda registrado para monitoreo de precios
- **Registrar affiliate links**: cuando el usuario abre un link de Skyscanner/Impact, se loguea la URL
- **n8n para workflows**: automatizaciones internas, emails, integraciones con APIs externas

En el futuro (próximos pasos):
- Detectar bajadas de precio y enviar push notifications al FCM token del usuario
- Enviar promociones personalizadas basadas en el historial de búsquedas

## Servicios

| Servicio | URL | Para qué |
|---|---|---|
| Python API | https://api.flypromociones.com | App Android |
| n8n | https://n8n.cordevs.com | Workflows internos |
| PostgreSQL | interno | Base de datos compartida |

---

## Levantar local

```bash
cp .env.example .env     # completar valores
docker compose up -d     # aplica docker-compose.override.yml automáticamente (HTTP, sin SSL)
```

Verificar:
```bash
curl http://localhost/api/v1/health
open http://localhost/n8n/
```

---

## Deploy en producción (VPS)

Ver **[DEPLOY.md](DEPLOY.md)** para la guía completa paso a paso.

Resumen:
```bash
# 1. Instalar Docker en el servidor
curl -fsSL https://get.docker.com | sh

# 2. Configurar el proyecto
cp .env.example .env && nano .env

# 3. Obtener certificados SSL (primera vez)
docker compose -f docker-compose.yml up -d postgres n8n api certbot
docker run --rm -p 80:80 -v flyp_certbot_certs:/etc/letsencrypt \
  certbot/certbot certonly --standalone \
  --email info@cordevs.com --agree-tos --no-eff-email \
  -d n8n.cordevs.com -d api.flypromociones.com

# 4. Levantar todo
docker compose -f docker-compose.yml up -d
```

---

## Comandos útiles

```bash
# Estado de los contenedores
docker compose -f docker-compose.yml ps

# Logs en vivo
docker compose -f docker-compose.yml logs -f api
docker compose -f docker-compose.yml logs -f n8n

# Reiniciar un servicio
docker compose -f docker-compose.yml restart api
```

---

## Backups

Los backups son dumps de PostgreSQL comprimidos con gzip.

```bash
# Hacer backup manual
./scripts/backup.sh
# → guarda en ./backups/fly_db_YYYYMMDD_HHMMSS.sql.gz
# → borra automáticamente los de más de 30 días

# Restaurar
./scripts/restore.sh backups/fly_db_20260101_120000.sql.gz
```

### Backup automático diario (recomendado en producción)

```bash
crontab -e
# Agregar:
0 3 * * * cd /root/FlyP && ./scripts/backup.sh >> /var/log/fly-backup.log 2>&1
```

---

## Agregar un nuevo servicio (ejemplo: Spring Boot)

Ver la sección correspondiente en [DEPLOY.md](DEPLOY.md#agregar-un-nuevo-servicio).

---

## Endpoints API

| Método | Path | Descripción |
|---|---|---|
| GET | `/api/v1/health` | Health check |
| POST | `/api/v1/profile` | Guarda perfil de usuario + crea price watch |
| POST | `/api/v1/impact?url=...` | Registra affiliate link abierto |
