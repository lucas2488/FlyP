# /db-check — Estado de la base de datos en producción

Conecta a la DB de producción y muestra un resumen del estado actual del sistema.

## Pasos

Ejecutar via SSH + psql en el contenedor de PostgreSQL:

```bash
ssh root@178.105.195.245 "cd /root/FlyP && source <(grep -E '^(DB_USER|DB_PASSWORD|DB_NAME)=' .env | sed 's/^/export /') && docker exec flyp-postgres-1 psql -U \$DB_USER -d \$DB_NAME -c \"QUERY\""
```

## Queries de estado rápido

```sql
-- Resumen general
SELECT 
  (SELECT COUNT(*) FROM user_profiles) as perfiles,
  (SELECT COUNT(*) FROM price_watches WHERE is_active=true) as watches_activos,
  (SELECT COUNT(*) FROM notification_queue WHERE status='pending') as notifs_pending,
  (SELECT COUNT(*) FROM notification_queue WHERE status='sent' AND sent_at >= NOW()-INTERVAL '24h') as notifs_24h,
  (SELECT COUNT(*) FROM campaigns WHERE status='draft') as campanas_draft;

-- Notificaciones recientes (últimas 2h)
SELECT id, user_id, origin, destination, drop_level, status, sent_at
FROM notification_queue
WHERE created_at >= NOW() - INTERVAL '2h'
ORDER BY created_at DESC LIMIT 20;

-- FCM tokens duplicados (debería ser 0)
SELECT COUNT(*) as duplicados FROM (
  SELECT fcm_token FROM user_profiles GROUP BY fcm_token HAVING COUNT(*) > 1
) t;

-- Campañas que no se ejecutaron
SELECT id, name, status, scheduled_at FROM campaigns 
WHERE status='draft' ORDER BY scheduled_at;
```

## Notas

- Los `user_id` en notification_queue y price_watches son FCM tokens (no user IDs reales)
- Si hay duplicados de fcm_token → bug crítico, ejecutar migración de limpieza
- Las campañas en draft que tienen scheduled_at pasado no se ejecutaron aún (bug conocido)
