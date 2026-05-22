#!/bin/bash
# Obtiene los certificados SSL por primera vez en producción.
# Correr UNA sola vez antes de levantar el stack completo.
# Requisito: los subdominios deben apuntar al IP del servidor antes de correr esto.

set -e

if [ ! -f .env ]; then
  echo "Error: no existe .env — copiá .env.example y completalo primero"
  exit 1
fi

source .env

if [ -z "$DOMAIN" ] || [ -z "$CERTBOT_EMAIL" ]; then
  echo "Error: DOMAIN y CERTBOT_EMAIL son requeridos en .env"
  exit 1
fi

echo "▶ Levantando nginx en modo HTTP para obtener certificados..."
docker compose -f docker-compose.yml up -d nginx

echo "▶ Esperando que nginx esté listo..."
sleep 5

echo "▶ Obteniendo certificado para n8n.${DOMAIN}..."
docker compose -f docker-compose.yml run --rm certbot certonly \
  --webroot -w /var/www/certbot \
  --email "$CERTBOT_EMAIL" \
  --agree-tos \
  --no-eff-email \
  -d "n8n.${DOMAIN}"

echo "▶ Obteniendo certificado para api.${DOMAIN}..."
docker compose -f docker-compose.yml run --rm certbot certonly \
  --webroot -w /var/www/certbot \
  --email "$CERTBOT_EMAIL" \
  --agree-tos \
  --no-eff-email \
  -d "api.${DOMAIN}"

echo "▶ Reiniciando nginx con SSL habilitado..."
docker compose -f docker-compose.yml restart nginx

echo ""
echo "✅ SSL listo. Ahora podés levantar el stack completo:"
echo "   docker compose -f docker-compose.yml up -d"
