#!/bin/bash
# Restaura un backup de PostgreSQL.
# Uso: ./scripts/restore.sh backups/fly_db_20260101_120000.sql.gz

set -e

if [ -z "$1" ]; then
  echo "Uso: $0 <archivo_backup.sql.gz>"
  echo ""
  echo "Backups disponibles:"
  ls -lh "$(dirname "$0")/../backups/"*.sql.gz 2>/dev/null || echo "  (ninguno)"
  exit 1
fi

BACKUP_FILE="$1"

if [ ! -f "$BACKUP_FILE" ]; then
  echo "Error: no se encontró el archivo $BACKUP_FILE"
  exit 1
fi

if [ ! -f .env ]; then
  echo "Error: no existe .env"
  exit 1
fi

source .env

echo "⚠️  Esto va a REEMPLAZAR la base de datos '${DB_NAME}' con el backup:"
echo "   $BACKUP_FILE"
echo ""
read -p "¿Confirmás? (escribí 'si' para continuar): " CONFIRM

if [ "$CONFIRM" != "si" ]; then
  echo "Cancelado."
  exit 0
fi

echo "▶ Restaurando backup..."

gunzip -c "$BACKUP_FILE" | docker compose exec -T postgres psql \
  -U "$DB_USER" \
  -d "$DB_NAME" \
  --no-password

echo "✅ Restauración completada."
