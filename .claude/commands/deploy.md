# /deploy — Deploy al servidor de producción

Copia los archivos modificados al VPS, rebuilda la imagen Docker y reinicia la API.
Verifica que el startup fue exitoso y muestra los últimos logs.

## Pasos

1. Detectar qué archivos Python cambiaron desde el último commit (`git diff --name-only`)
2. Copiar solo los archivos modificados al servidor con `scp`
3. Si hay nuevas migraciones en `alembic/versions/`, copiarlas también
4. Hacer `docker compose build api` en el servidor
5. Hacer `docker compose up -d api`
6. Esperar 10 segundos y mostrar los últimos 30 líneas de logs
7. Verificar que no haya errores de migración ni de startup

## Servidor

```
Host: root@178.105.195.245
Directorio: /root/FlyP
```

## Comando base

```bash
# Copiar archivo individual
scp app/routers/profile.py root@178.105.195.245:/root/FlyP/app/routers/profile.py

# Rebuild y restart
ssh root@178.105.195.245 "cd /root/FlyP && docker compose -f docker-compose.yml build api && docker compose -f docker-compose.yml up -d api"

# Ver resultado
ssh root@178.105.195.245 "sleep 8 && docker compose -f /root/FlyP/docker-compose.yml logs api --tail=40"
```

## Notas

- Las migraciones Alembic corren automáticamente en el startup
- El `.env` nunca se toca — vive en el servidor
- Si hay cambios en `requirements.txt`, el build tarda más (reinstala dependencias)
- El dashboard se despliega por separado con `firebase deploy --only hosting` desde `FlyMarketingDashboard/`
