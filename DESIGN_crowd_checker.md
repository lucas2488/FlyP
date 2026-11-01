# Diseño: búsquedas de precio hechas por los celulares de los usuarios

> Estado: **backend y dashboard implementados (01/11/2026)**. Falta: FlyAdmin con reservas, Android, iOS.
> Código: `app/services/price_alert_claims.py`, `price_alert_results.py`, `app_settings.py`,
> `checker_overview.py`, `app/routers/price_alerts.py`. Dashboard: página "Buscador de precios".

## Problema

Hoy el precio de cada alerta lo consulta a mano la app de admin (FlyAdmin): pide
`GET /checker/searches`, busca en Skyscanner y manda `POST /checker/results`.
Con 12 alertas está bien; con 500 alertas y dos pasadas por día no escala.

La app tiene ~400 usuarios por día con headers válidos de Skyscanner. Mientras el usuario
no está buscando, su app puede ir consultando en segundo plano búsquedas de alertas
(no necesariamente suyas) y mandarle los precios al servidor.

## Conceptos

- **Búsqueda** = `search_key`: varias alertas idénticas (ruta, fechas, pasajeros, clase,
  mercado) comparten una búsqueda; se consulta una vez y el precio se aplica a todas.
- **Reserva (claim)**: el servidor le asigna una búsqueda a un aparato por **10 minutos**.
  Mientras está reservada, nadie más la recibe. Si vence sin resultado, vuelve a estar
  disponible.
- **Búsqueda completa**: en Skyscanner una búsqueda es un `POST` que se repite (poll) hasta
  que la respuesta viene `complete`. Una reserva = una búsqueda completa (varios pedidos
  a Skyscanner adentro) = **un solo resultado** al servidor.

## Backend

### 1. Tabla `price_alert_claims`

`claim_id`, `search_key`, `device_id` (o worker), `source` (admin | app),
`claimed_at`, `expires_at`, `status` (open | done | expired | failed).

### 2. `POST /checker/claim`

- Body: `{ "limit": 1 }` (la app de usuarios pide 1; FlyAdmin puede pedir un lote, p. ej. 6).
- Elige primero las bajadas raras esperando verificación (nunca al aparato que la informó) y
  después las búsquedas pendientes más viejas (no consultadas en `RECHECK_AFTER`, hoy 10 h,
  y sin reserva vigente). Las reserva de forma atómica con un índice único parcial
  (`search_key` WHERE `status='open'`) + `INSERT … ON CONFLICT DO NOTHING`: dos pedidos
  simultáneos nunca reciben la misma.
- Cada reserva trae `purpose`: `check` (normal) o `verify` (confirmar una bajada rara).
- Respuesta:
  ```json
  { "claims": [ { "claim_id": "...", "expires_at": "...", "search_key": "s_...",
                  "origin": {...}, "destination": {...}, "trip_type": "...", "date_mode": "...",
                  "departure_date": "...", "return_date": "...", "month": null,
                  "adults": 1, "child_ages": [], "cabin_class": "ECONOMY",
                  "currency": "ARS", "country": "AR" } ],
    "next_poll_after_seconds": 45 }
  ```
  `claims: []` + `next_poll_after_seconds: null` → no hay nada pendiente: dejar de pedir.
- Si el interruptor general está apagado o el aparato llegó a su tope: `claims: []`.
- `POST` y no `GET` porque cambia estado (un reintento automático de un GET reservaría de más).

### 3. `POST /checker/results` con `claim_id`

- Mismo endpoint y formato de hoy, más `claim_id` opcional por resultado.
- La app de usuarios está obligada a mandar `claim_id` (si no: `claim_required`).
- Con `claim_id`: se valida que la reserva sea de ese aparato y no haya vencido; se marca
  `done` (o `failed` si llega `error`). Un error libera la búsqueda para otro.
- Sin `claim_id`: funciona como hoy (FlyAdmin sin reservas sigue andando).
- Solo se manda precio de búsquedas **completas**. Si no llega a `complete` (corte,
  error, timeout) se manda `error: "incompleta"` sin precio.

### 4. Autenticación de la app de usuarios

- **Firebase App Check**: solo la app real, firmada, desde un aparato real.
- Separada de `X-Checker-Key` (que queda para FlyAdmin).
- Identidad del aparato: Firebase Installation ID (el mismo que usa `users/{uid}/devices`).

### 5. Topes y ritmo

- `next_poll_after_seconds` lo decide el servidor (pausa entre búsquedas completas).
- Tope por aparato por sesión y por día (p. ej. 10 por sesión, 30 por día).
- Todo configurable desde el dashboard (ver 6).

### 6. Ajustes editables desde el dashboard (tabla `app_settings`, sin redeploy)

| Ajuste | Default | Qué hace |
|---|---|---|
| `crowd_checker_enabled` | prendido | Interruptor general: apagado → `claim` no reparte nada (kill switch). |
| `claim_minutes` | 10 | Duración de la reserva. |
| `poll_interval_seconds` | 45 | Pausa entre búsquedas que el servidor le indica a la app. |
| `max_per_session` / `max_per_day` | 10 / 30 | Topes por aparato. |
| `double_check_enabled` | **apagado** | Doble verificación de bajadas raras. |
| `double_check_threshold_pct` | 30 | Bajada a partir de la cual se considera "rara". |

Doble verificación (cuando está prendida): una bajada de la app mayor al umbral no toca la
alerta (`status: pending_verification`); la búsqueda se le da primero a otro aparato (o a
FlyAdmin) y se avisa solo si el segundo precio está a ±10 %. Sin confirmar en 24 h, se descarta.
FlyAdmin es confiable: sus resultados no se retienen y resuelven las verificaciones pendientes.

Endpoints del dashboard: `GET/PUT /analytics/checker/settings`, `GET /analytics/checker/overview?days=`.

### 7. Dashboard

- Ajustes de la sección 6.
- Bajadas "raras" (sobre el umbral) y de qué aparatos vinieron, aunque la doble
  verificación esté apagada: es lo que sirve para decidir prenderla.
- Qué aparato consultó cada búsqueda, reservas vencidas / fallidas, búsquedas por día
  separadas en admin vs. usuarios.
- Comparación de precios de usuarios vs. FlyAdmin para la misma búsqueda.

## FlyAdmin

- Antes de buscar: `POST /checker/claim` con `limit` = cuántas va a hacer (p. ej. 6).
- Busca esas, manda cada resultado a `POST /checker/results` con su `claim_id`.
- Así, si FlyAdmin y los celulares buscan al mismo tiempo, nadie repite.

## Android (todos los usuarios, detrás del interruptor del servidor)

- Pool en segundo plano (corrutina en `Dispatchers.IO`), con menos prioridad que la
  búsqueda del usuario:
  1. La app terminó de cargar y tiene headers válidos.
  2. `POST /checker/claim` (limit 1).
  3. Búsqueda completa en Skyscanner (POST + poll hasta `complete`) con el **mercado de la
     alerta** (`country` + `currency`), no el del celular, respetando los tiempos entre
     pedidos como en una búsqueda normal.
  4. `POST /checker/results` con `claim_id`, `price` (mínimo `price.raw`), `currency`,
     `itinerary_id`; o `error` si no completó o no hubo vuelos.
  5. Esperar `next_poll_after_seconds` y volver a 2.
- Se pausa / corta si: el usuario empieza a buscar, la app pasa a segundo plano, el
  servidor devuelve `claims: []`, o Skyscanner da error (429, captcha, etc.) — en ese caso
  se corta el pool en esa sesión para no quemar los headers del usuario.
- iOS: más adelante, después de validar con Android.

## Fuera de este trabajo

- Política de privacidad: la maneja Lucas. No se toca sin su permiso.
