# Convenciones del backend — reglas no negociables

## Identidad de dispositivo

El FCM token ES el usuario. No hay más concepto de identidad.

```python
# ✅ Correcto
select(UserProfile).where(UserProfile.fcm_token == token)

# ❌ Nunca hacer esto
select(UserProfile).where(UserProfile.user_id == something)
```

Cuando un servicio tiene un `item.user_id` de `notification_queue` o `price_watches`,
ese valor ES el fcm_token — buscar UserProfile por `UserProfile.fcm_token == item.user_id`.

`user_id` en `user_profiles` siempre es NULL. No asignar valor nunca.

## Notificaciones — nunca enviar directo

Las notificaciones NO se envían desde los endpoints de la API. El flujo obligatorio es:

```
endpoint → notification_engine.evaluate_price_drop() → notification_queue (pending)
APScheduler job → notification_dispatcher → FCM
```

La única excepción es `welcome_service` y `reengagement_service` que tienen su propio
dispatcher. Las campañas usan `campaign_engine`.

## firebase_service — funciones síncronas

`firebase_service.send_notification()` y `firebase_service.send_to_topic()` son funciones
**síncronas** que retornan `bool`. NO usar `await`:

```python
# ✅ Correcto
success = firebase_service.send_notification(token, title, body, data)

# ❌ Rompe en runtime (await bool → TypeError)
await firebase_service.send_notification(token, title, body, data)
```

## Migraciones

- Siempre crear archivo nuevo en `alembic/versions/` — nunca editar uno existente
- Nombre: `NNNN_descripcion_corta.py` (ver último número con `ls alembic/versions/`)
- El `downgrade()` puede ser `pass` si la migración destruye datos o cambia PK
- Las migraciones corren automáticamente en el startup del container
- Si una migración modifica user_profiles o tablas hijas, verificar que respeta
  el modelo de identidad (fcm_token, no user_id)

## Cooldowns en price_watches

Los cooldowns se guardan en `price_watches` por nivel:
- `last_notified_urgent_at`, `last_notified_strong_at`, `last_notified_soft_at`
- El campo `user_id` en price_watches = fcm_token del dispositivo
- El cooldown solo se actualiza cuando el envío FCM fue exitoso (status=sent)
- Si el envío falla, el cooldown NO se actualiza → se puede reintentar

## Queries en servicios

Los servicios (campaign_engine, welcome_service, etc.) crean su propia sesión:
```python
async with AsyncSessionLocal() as db:
    ...
```
Los routers reciben la sesión via `Depends(get_db)`.

Nunca compartir sesiones entre routers y services. Nunca pasar `db` como argumento
de un router a un service que luego lo pasa a otro service — cada service crea la suya.

## asyncio.create_task y commits

Si usás `asyncio.create_task()` para correr algo en background que lee de la DB:
```python
# ✅ Correcto — commit primero, task después
await db.commit()
asyncio.create_task(mi_funcion(id))

# ❌ Race condition — la task puede leer antes del commit
asyncio.create_task(mi_funcion(id))
await db.commit()
```

## Seguridad en la API

- El endpoint `/api/v1/profile` no requiere autenticación (el app lo llama libremente)
- Los endpoints `/api/v1/analytics/*` y `/api/v1/admin/*` requieren header `X-API-Key`
- El `firebase-service-account.json` NUNCA va a git (está en .gitignore)
- El `.env` NUNCA va a git, vive solo en el servidor en `/root/FlyP/.env`
