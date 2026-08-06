# Guía de deploy desde cero

Paso a paso para levantar el stack completo en un VPS nuevo con Ubuntu Server.
Tiempo estimado: ~20 minutos.

---

## Requisitos previos

- VPS con Ubuntu 22.04 / 24.04 / 26.04 (funciona en AMD64 y ARM64)
- Acceso root por SSH
- Dos dominios apuntando a la IP del servidor:
  - `n8n.cordevs.com` → IP del servidor
  - `api.flypromociones.com` → IP del servidor
- Esperar propagación DNS (~5-10 min) antes de continuar

---

## 1. Conectarse al servidor

```bash
ssh root@IP_DEL_SERVIDOR
```

---

## 2. Instalar Docker

```bash
apt update && apt upgrade -y
curl -fsSL https://get.docker.com | sh

# Verificar
docker --version
docker compose version
```

---

## 3. Configurar firewall

```bash
ufw allow 22
ufw allow 80
ufw allow 443
ufw --force enable
ufw status
```

---

## 4. Clonar el proyecto

```bash
mkdir -p /root/FlyP
cd /root/FlyP
# Opción A: clonar desde GitHub (si el repo está actualizado)
git clone git@github.com:lucas2488/FlyP.git .

# Opción B: copiar archivos via SCP desde la Mac
# (ver sección "Actualizar sin GitHub" al final)
```

---

## 5. Crear el archivo .env

```bash
cp .env.example .env
nano .env
```

Completar todos los valores. Los campos obligatorios:

```
DB_USER=fly_user
DB_PASSWORD=<password seguro>          # generá con: openssl rand -hex 16
DB_NAME=fly_db

N8N_BASIC_AUTH_USER=admin
N8N_BASIC_AUTH_PASSWORD=<password>     # el que uses para entrar a n8n

API_SECRET_KEY=<secret>                # generá con: openssl rand -hex 32

N8N_DOMAIN=n8n.cordevs.com
API_DOMAIN=api.flypromociones.com

SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=lucas.rodriguezkelly@gmail.com
SMTP_PASS=<app password de 16 letras>  # ver sección Gmail más abajo
SMTP_SENDER="FlyPromociones <lucas.rodriguezkelly@gmail.com>"

CERTBOT_EMAIL=info@cordevs.com
```

Guardar: `Ctrl+O` → Enter → `Ctrl+X`

---

## 6. Obtener certificados SSL (primera vez)

El stack necesita los certs ANTES de que nginx pueda arrancar.
Se usa certbot en modo standalone (no necesita nginx corriendo):

```bash
# Levantar solo los servicios internos primero
docker compose -f docker-compose.yml up -d postgres n8n api certbot

# Obtener certs para los dos dominios de una sola vez
docker run --rm \
  -p 80:80 \
  -v flyp_certbot_certs:/etc/letsencrypt \
  certbot/certbot certonly --standalone \
  --email info@cordevs.com \
  --agree-tos --no-eff-email \
  -d n8n.cordevs.com \
  -d api.flypromociones.com
```

Deberías ver: `Successfully received certificate.`

> **Nota**: Certbot genera un solo certificado SAN para los dos dominios.
> Los archivos quedan en `/etc/letsencrypt/live/n8n.cordevs.com/`.
> Nginx usa ese mismo path para ambos dominios.

---

## 7. Levantar el stack completo

```bash
docker compose -f docker-compose.yml up -d
```

Verificar que todos estén `Up`:
```bash
docker compose -f docker-compose.yml ps
```

Esperar ~30 segundos y probar:
```bash
curl https://api.flypromociones.com/api/v1/health
# → {"status":"ok","service":"fly-backend"}

curl -I https://n8n.cordevs.com/
# → HTTP/2 200
```

---

## 8. Configurar backup automático

```bash
chmod +x /root/FlyP/scripts/backup.sh

# Agregar al crontab — backup diario a las 3am
crontab -e
```

Agregar esta línea:
```
0 3 * * * cd /root/FlyP && ./scripts/backup.sh >> /var/log/fly-backup.log 2>&1
```

Los backups se guardan en `/root/FlyP/backups/` con formato `fly_db_YYYYMMDD_HHMMSS.sql.gz`.
Los de más de 30 días se borran automáticamente.

---

## Gmail App Password (para SMTP)

Para que n8n pueda enviar emails (recuperar contraseña, notificaciones):

1. Ir a `myaccount.google.com/security`
2. Activar **Verificación en 2 pasos** si no está activa
3. Ir a `myaccount.google.com/apppasswords`
4. Nombre: `n8n` → clic en **Crear**
5. Copiar las 16 letras (sin espacios) → pegar en `SMTP_PASS` del `.env`

---

## Actualizar la API tras cambios de código

> ⚠️ **REGLA DE ORO: commitear a git ANTES de deployar.**
> El server (`/root/FlyP`) **no es un checkout de git** — son archivos sueltos que
> se actualizan por `scp`. Si deployás por `scp` sin commitear, git queda
> desactualizado respecto a lo que corre en producción. Eso ya pasó: features
> vivas (open rate, template-health) y hasta bugs quedaron **invisibles en el
> historial**, y un deploy desde git les hubiera hecho rollback. Para evitarlo,
> el orden es **siempre**: commit + push → scp → verificar que el server == git.

### Paso a paso

```bash
# 1. Commitear y pushear PRIMERO (git = fuente de verdad)
git add -A
git commit -m "descripción del cambio"
git push origin main

# 2. Backup del archivo/dir en el server (por si hay que revertir)
ssh root@IP "cp /root/FlyP/app/routers/X.py /root/FlyP/app/routers/X.py.bak_$(date +%Y%m%d_%H%M%S)"

# 3. Copiar SOLO los archivos cambiados (no 'scp -r app/' a ciegas:
#    puede pisar cambios que existan únicamente en el server)
scp app/routers/X.py root@IP:/root/FlyP/app/routers/X.py

# 4. Rebuild + restart del contenedor api
#    (el CMD del Dockerfile corre 'alembic upgrade head' al arrancar,
#     así que las migraciones nuevas se aplican solas)
ssh root@IP "cd /root/FlyP && docker compose -f docker-compose.yml build api && docker compose -f docker-compose.yml up -d api"

# 5. VERIFICAR que el server quedó igual a git (hash por hash)
git show HEAD:app/routers/X.py | shasum -a 256
ssh root@IP "shasum -a 256 /root/FlyP/app/routers/X.py"
#   → los dos hashes deben coincidir

# 6. Verificar salud + comportamiento
curl https://api.flypromociones.com/api/v1/health
```

### ⚠️ Antes de tocar un archivo que ya existe en el server

Como el server puede tener cambios que **no están en git** (deployados por scp en
el pasado), **nunca sobrescribas a ciegas**. Primero compará:

```bash
# ¿El archivo del server coincide con git HEAD?
git show HEAD:app/routers/X.py | shasum -a 256
ssh root@IP "shasum -a 256 /root/FlyP/app/routers/X.py"
```

- **Coinciden** → editás tu copia local y la subís tranquilo.
- **NO coinciden** → el server tiene algo que git no. Bajá el archivo del server
  (`scp root@IP:/root/FlyP/... /tmp/`), diffealo contra tu local, y aplicá tu
  cambio **encima de la versión del server** para no perder nada. Después
  commiteá esa versión reconciliada para que git vuelva a estar al día.

### Migraciones de base de datos

Si el cambio incluye una migración Alembic nueva:

```bash
scp alembic/versions/00XX_descripcion.py root@IP:/root/FlyP/alembic/versions/
# El rebuild+restart del paso 4 la aplica solo (alembic upgrade head).
# Verificar:
ssh root@IP "docker exec flyp-postgres-1 psql -U fly_user -d fly_db -t -c 'SELECT version_num FROM alembic_version;'"
```

### Mejora recomendada (a futuro)

Convertir `/root/FlyP` en un checkout de git para deployar con `git pull` +
rebuild en vez de `scp` suelto. Elimina de raíz el problema de drift.

---

## Renovación de certificados SSL

Certbot renueva automáticamente cada 12h (ver `docker-compose.yml`, servicio `certbot`).
Para renovar manualmente:

```bash
docker compose -f docker-compose.yml run --rm certbot renew
docker compose -f docker-compose.yml restart nginx
```

---

## Troubleshooting frecuente

**nginx no arranca:**
```bash
docker compose -f docker-compose.yml logs nginx --tail=20
# Causa más común: certs no existen → correr paso 6
```

**API no arranca:**
```bash
docker compose -f docker-compose.yml logs api --tail=20
# Causa más común: variables extra en .env no reconocidas por Pydantic
# Solución: ya está corregido con extra="ignore" en config.py
```

**n8n no guarda workflows:**
```bash
docker compose -f docker-compose.yml logs n8n --tail=20
# Verificar que postgres esté healthy antes de que n8n arranque
```

**Restaurar backup:**
```bash
./scripts/restore.sh backups/fly_db_20260101_120000.sql.gz
```
