import logging
from firebase_admin import messaging

logger = logging.getLogger(__name__)


def send_notification(fcm_token: str, title: str, body: str, data: dict) -> bool:
    """
    Envía una notificación FCM a un dispositivo como mensaje data-only (sin campo notification).
    Esto garantiza que onMessageReceived() siempre sea llamado en Android, incluso con la app
    en background, permitiendo que la app construya la notificación del sistema con los extras
    necesarios para el tracking de apertura (notification_id → POST /notifications/{id}/opened).
    Retorna True si exitoso.
    """
    full_data = {**data, "title": title, "body": body}
    message = messaging.Message(
        data={k: str(v) for k, v in full_data.items()},
        android=messaging.AndroidConfig(priority="high"),
        token=fcm_token,
    )
    try:
        response = messaging.send(message)
        logger.info(f"FCM sent OK: {response}")
        return True
    except Exception as e:
        logger.error(f"FCM send failed for token {fcm_token[:20]}...: {e}")
        return False


def send_to_topic(topic: str, title: str, body: str, data: dict) -> bool:
    """
    Envía una notificación a un FCM topic (broadcast a todos los suscriptores).
    Usado para campañas de feriados/efemérides (ej: topic='flypromociones_AR').
    Una sola llamada a la API de Firebase entrega a todos los dispositivos suscritos.
    Retorna True si exitoso.
    """
    full_data = {**data, "title": title, "body": body}
    message = messaging.Message(
        # Bloque notification: Android muestra la notif aunque la app esté cerrada.
        # Sin esto, el mensaje era data-only y no se veía con la app killeada.
        notification=messaging.Notification(title=title, body=body),
        data={k: str(v) for k, v in full_data.items()},
        android=messaging.AndroidConfig(priority="high"),
        topic=topic,
    )
    try:
        response = messaging.send(message)
        logger.info(f"FCM topic '{topic}' sent OK: {response}")
        return True
    except Exception as e:
        logger.error(f"FCM topic send failed for topic '{topic}': {e}")
        return False


def send_multicast(tokens: list[str], title: str, body: str, data: dict) -> tuple[int, int]:
    """
    Envía a múltiples tokens (hasta 500 por batch).
    Retorna (success_count, failure_count).
    """
    if not tokens:
        return 0, 0
    full_data = {**data, "title": title, "body": body}
    message = messaging.MulticastMessage(
        data={k: str(v) for k, v in full_data.items()},
        android=messaging.AndroidConfig(priority="high"),
        tokens=tokens,
    )
    try:
        response = messaging.send_each_for_multicast(message)
        return response.success_count, response.failure_count
    except Exception as e:
        logger.error(f"FCM multicast failed: {e}")
        return 0, len(tokens)
