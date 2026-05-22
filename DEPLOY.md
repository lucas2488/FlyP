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

```bash
# Desde la Mac, copiar los archivos modificados
scp -r app/ root@IP:/root/FlyP/
scp requirements.txt root@IP:/root/FlyP/

# En el servidor
cd /root/FlyP
docker compose -f docker-compose.yml build api
docker compose -f docker-compose.yml up -d api
```

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
