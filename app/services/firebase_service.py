import logging
from firebase_admin import messaging

logger = logging.getLogger(__name__)


def send_notification(fcm_token: str, title: str, body: str, data: dict) -> bool:
    """
    Envía una notificación FCM a un dispositivo.
    El dict `data` debe tener las keys que espera FlightNotification.kt:
      link, origin, destination, description, imageUrl, price, company
    Retorna True si exitoso.
    """
    message = messaging.Message(
        notification=messaging.Notification(title=title, body=body),
        data={k: str(v) for k, v in data.items()},
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
    message = messaging.Message(
        notification=messaging.Notification(title=title, body=body),
        data={k: str(v) for k, v in data.items()},
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
    message = messaging.MulticastMessage(
        notification=messaging.Notification(title=title, body=body),
        data={k: str(v) for k, v in data.items()},
        tokens=tokens,
    )
    try:
        response = messaging.send_each_for_multicast(message)
        return response.success_count, response.failure_count
    except Exception as e:
        logger.error(f"FCM multicast failed: {e}")
        return 0, len(tokens)
