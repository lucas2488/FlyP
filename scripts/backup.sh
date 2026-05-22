#!/bin/bash
# Hace dump de PostgreSQL y lo guarda en ./backups/ con fecha y hora.

set -e

if [ ! -f .env ]; then
  echo "Error: no existe .env"
  exit 1
fi

source .env

BACKUP_DIR="$(dirname "$0")/../backups"
mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
FILENAME="${BACKUP_DIR}/fly_db_${TIMESTAMP}.sql.gz"

echo "▶ Haciendo backup de ${DB_NAME}..."

docker compose exec -T postgres pg_dump \
  -U "$DB_USER" \
  -d "$DB_NAME" \
  --no-password \
  | gzip > "$FILENAME"

echo "✅ Backup guardado en: $FILENAME"
echo "   Tamaño: $(du -sh "$FILENAME" | cut -f1)"

# Borra backups de más de 30 días
find "$BACKUP_DIR" -name "*.sql.gz" -mtime +30 -delete
echo "   Backups viejos (+30 días) eliminados."
