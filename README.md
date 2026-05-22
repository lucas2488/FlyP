# FlyP Backend

Stack: FastAPI + n8n + PostgreSQL + Nginx + Certbot

## Servicios

| Servicio | Local | Producción |
|---|---|---|
| API Python | http://localhost/api/ | https://api.DOMAIN |
| n8n | http://localhost/n8n/ | https://n8n.DOMAIN |
| PostgreSQL | localhost:5432 | interno (no expuesto) |

---

## Levantar local

```bash
cp .env.example .env     # editar con tus valores
docker compose up -d     # aplica docker-compose.override.yml automáticamente
```

Verificar:
```bash
curl http://localhost/api/v1/health
# → {"status":"ok","service":"fly-backend"}

open http://localhost/n8n/
```

---

## Levantar en producción

**Requisitos previos:**
- Los DNS de `n8n.DOMAIN` y `api.DOMAIN` apuntan al IP del servidor
- Docker y Docker Compose instalados

```bash
# 1. Clonar y configurar
git clone https://github.com/lucas2488/FlyP.git
cd FlyP
cp .env.example .env
nano .env   # completar DOMAIN, CERTBOT_EMAIL y passwords

# 2. Obtener certificados SSL (solo la primera vez)
chmod +x scripts/*.sh
./scripts/init-ssl.sh

# 3. Levantar el stack completo
docker compose -f docker-compose.yml up -d
```

---

## Comandos útiles

### Ver logs
```bash
docker compose logs -f              # todos los servicios
docker compose logs -f api          # solo la API
docker compose logs -f n8n          # solo n8n
docker compose logs -f nginx        # solo nginx
```

### Reiniciar un servicio
```bash
docker compose restart api
docker compose restart n8n
```

### Ver estado
```bash
docker compose ps
```

### Actualizar la API (nuevo deploy)
```bash
git pull
docker compose -f docker-compose.yml build api
docker compose -f docker-compose.yml up -d api
```

---

## Backup y restore

### Hacer backup
```bash
./scripts/backup.sh
# Guarda en ./backups/fly_db_YYYYMMDD_HHMMSS.sql.gz
# Los backups de +30 días se borran automáticamente
```

### Restaurar backup
```bash
./scripts/restore.sh backups/fly_db_20260101_120000.sql.gz
```

### Backup automático (cron en el servidor)
```bash
# Agregar al crontab: backup diario a las 3am
crontab -e
# 0 3 * * * cd /ruta/FlyP && ./scripts/backup.sh >> /var/log/fly-backup.log 2>&1
```

---

## Agregar un nuevo servicio (ejemplo: Spring Boot)

**1. Agregar al `docker-compose.yml`:**
```yaml
  spring-app:
    image: tu-imagen:latest   # o build: ./spring-app
    restart: unless-stopped
    environment:
      SPRING_DATASOURCE_URL: jdbc:postgresql://postgres:5432/${DB_NAME}
      SPRING_DATASOURCE_USERNAME: ${DB_USER}
      SPRING_DATASOURCE_PASSWORD: ${DB_PASSWORD}
    depends_on:
      postgres:
        condition: service_healthy
```

**2. Agregar el subdominio en `nginx/templates/default.conf.template`:**
```nginx
server {
    listen 443 ssl;
    server_name app.${DOMAIN};

    ssl_certificate     /etc/letsencrypt/live/app.${DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/app.${DOMAIN}/privkey.pem;

    location / {
        proxy_pass http://spring-app:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

**3. Obtener certificado para el nuevo subdominio:**
```bash
docker compose run --rm certbot certonly \
  --webroot -w /var/www/certbot \
  --email $CERTBOT_EMAIL --agree-tos --no-eff-email \
  -d app.DOMAIN

docker compose restart nginx
```

**4. Levantar:**
```bash
docker compose -f docker-compose.yml up -d spring-app
```

---

## Endpoints API

| Método | Path | Descripción |
|---|---|---|
| GET | `/api/v1/health` | Health check |
| POST | `/api/v1/profile` | Guarda perfil de usuario + crea price watch |
| POST | `/api/v1/impact?url=...` | Registra affiliate link abierto |
