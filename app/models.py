from datetime import date, datetime
from sqlalchemy import BigInteger, Boolean, Date, Float, ForeignKey, Integer, String, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class UserProfile(Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    fcm_token: Mapped[str | None] = mapped_column(Text)
    app_version: Mapped[str | None] = mapped_column(String(20))
    device_model: Mapped[str | None] = mapped_column(String(100))
    os_version: Mapped[str | None] = mapped_column(String(50))
    language: Mapped[str | None] = mapped_column(String(10))
    timezone: Mapped[str | None] = mapped_column(String(50))
    selected_country: Mapped[str | None] = mapped_column(String(10))
    selected_currency: Mapped[str | None] = mapped_column(String(10))
    last_app_open: Mapped[int | None] = mapped_column(BigInteger)
    last_updated_at: Mapped[int | None] = mapped_column(BigInteger)
    last_search_date: Mapped[int | None] = mapped_column(BigInteger)
    last_search_origin: Mapped[str | None] = mapped_column(String(100))
    last_search_destination: Mapped[str | None] = mapped_column(String(100))
    last_search_origin_iata: Mapped[str | None] = mapped_column(String(10))
    last_search_destination_iata: Mapped[str | None] = mapped_column(String(10))
    last_search_origin_geo_id: Mapped[str | None] = mapped_column(String(50))
    last_search_origin_country: Mapped[str | None] = mapped_column(String(10))
    last_search_destination_geo_id: Mapped[str | None] = mapped_column(String(50))
    last_search_destination_country: Mapped[str | None] = mapped_column(String(10))
    last_flight_status_click_date: Mapped[int | None] = mapped_column(BigInteger)
    last_checkin_click_date: Mapped[int | None] = mapped_column(BigInteger)
    total_searches: Mapped[int] = mapped_column(Integer, default=0)
    total_flight_status_clicks: Mapped[int] = mapped_column(Integer, default=0)
    total_checkin_clicks: Mapped[int] = mapped_column(Integer, default=0)
    skyscanner_headers: Mapped[str | None] = mapped_column(Text)  # JSON string
    # Segmentación — calculada por el Android, enviada en cada POST /profile
    user_segment: Mapped[str | None] = mapped_column(String(50))   # heavy_searcher | casual | inactive | …
    engagement_score: Mapped[float | None] = mapped_column(Float)  # 0.0 – 100.0
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class PriceWatch(Base):
    __tablename__ = "price_watches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    origin: Mapped[str] = mapped_column(String(10))
    destination: Mapped[str] = mapped_column(String(10))
    trip_type: Mapped[str] = mapped_column(String(20))
    last_price: Mapped[float | None] = mapped_column(Float)
    last_checked: Mapped[datetime | None] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # Motor de oportunidad
    last_search_best_price: Mapped[float | None] = mapped_column(Float)
    last_selected_price: Mapped[float | None] = mapped_column(Float)
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_notified_soft_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_notified_strong_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_notified_urgent_at: Mapped[datetime | None] = mapped_column(DateTime)
    interest_score: Mapped[int] = mapped_column(Integer, default=0)
    notification_count: Mapped[int] = mapped_column(Integer, default=0)


class PriceHistory(Base):
    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    origin: Mapped[str] = mapped_column(String(10))
    destination: Mapped[str] = mapped_column(String(10))
    min_price: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(10), default="ARS")
    checked_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NotificationLog(Base):
    __tablename__ = "notification_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(String)
    type: Mapped[str | None] = mapped_column(String(50))
    origin: Mapped[str | None] = mapped_column(String(10))
    destination: Mapped[str | None] = mapped_column(String(10))
    price: Mapped[float | None] = mapped_column(Float)
    delivery_status: Mapped[str] = mapped_column(String(20), default="sent")
    sent_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ImpactLinkLog(Base):
    __tablename__ = "impact_link_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(String)
    url: Mapped[str] = mapped_column(Text)
    clicked_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AirportCache(Base):
    """
    Cache de aeropuertos vistos en búsquedas de usuarios.
    Se hace upsert cada vez que llega un POST /profile con IATA codes.
    Sirve para construir una base de datos local de aeropuertos con sus
    códigos de Skyscanner (iata, geoId, placeId) sin depender de APIs externas.
    """
    __tablename__ = "airport_cache"

    iata_code: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(100))
    country: Mapped[str | None] = mapped_column(String(10))
    geo_id: Mapped[str | None] = mapped_column(String(50))
    place_id: Mapped[str | None] = mapped_column(String(100))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    times_searched: Mapped[int] = mapped_column(Integer, default=1)


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    origin: Mapped[str] = mapped_column(String(10))
    destination: Mapped[str] = mapped_column(String(10))
    trip_type: Mapped[str] = mapped_column(String(20))
    snapshot_date: Mapped[str] = mapped_column(String(10))  # "2026-06-15"
    price_raw: Mapped[float] = mapped_column(Float)
    price_group: Mapped[str] = mapped_column(String(10))    # "low"
    currency: Mapped[str] = mapped_column(String(3))
    received_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class SearchEvent(Base):
    __tablename__ = "search_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    origin: Mapped[str] = mapped_column(String(10))
    destination: Mapped[str] = mapped_column(String(10))
    trip_type: Mapped[str | None] = mapped_column(String(20))
    best_price: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(3))
    event_type: Mapped[str] = mapped_column(String(30))    # "search_result" | "flight_selected"
    event_data: Mapped[str | None] = mapped_column(Text)   # JSON string (airline, date, etc.)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    notified_reengagement_at: Mapped[datetime | None] = mapped_column(DateTime)


class NotificationQueue(Base):
    __tablename__ = "notification_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    origin: Mapped[str] = mapped_column(String(10))
    destination: Mapped[str] = mapped_column(String(10))
    price_raw: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3))
    pct_drop: Mapped[float] = mapped_column(Float)
    reference_price: Mapped[float] = mapped_column(Float)
    trip_type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|sent|failed|skipped
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    error_msg: Mapped[str | None] = mapped_column(Text)
    drop_level: Mapped[str | None] = mapped_column(String(10))          # soft|strong|urgent
    notification_type: Mapped[str] = mapped_column(String(20), default="price_drop")
    opened_at: Mapped[datetime | None] = mapped_column(DateTime)


class UserFavorite(Base):
    """
    Rutas favoritas sincronizadas desde el Android (FavoriteSSDao).
    Se hace full-replace en cada POST /api/v1/favorites:
    se eliminan todas las del usuario y se insertan las nuevas.
    Nota: usa flyp_user_favorites para evitar colisión con tablas de n8n.
    """
    __tablename__ = "flyp_user_favorites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    origin_iata: Mapped[str] = mapped_column(String(10))
    destination_iata: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NotificationTemplate(Base):
    """
    Templates pre-generados de notificaciones con jerga local por país.
    Se selecciona uno al azar al momento del envío, filtrando por
    selected_country del usuario y drop_level de la notificación.

    country_code: AR | MX | CO | CL | ES | * (genérico, fallback)
    drop_level:   soft | strong | urgent | reengagement

    Variables disponibles en los templates:
      {origin}, {destination}, {pct}, {price}, {currency}
    """
    __tablename__ = "notification_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    country_code: Mapped[str] = mapped_column(String(5), index=True)
    drop_level: Mapped[str] = mapped_column(String(20))
    title_template: Mapped[str] = mapped_column(Text)
    body_template: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ---------------------------------------------------------------------------
# Sistema de campañas de marketing
# ---------------------------------------------------------------------------

class Campaign(Base):
    """
    Campaña de notificaciones masivas por segmento.
    status: draft → scheduled → sending → sent | failed
    campaign_type: 'route' (ruta específica) | 'top_auto' (top rutas automático) | 'category' (tag)
    """
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200))
    segment: Mapped[str | None] = mapped_column(String(50))        # heavy_searcher | casual | …
    status: Mapped[str] = mapped_column(String(20), default="draft")
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    campaign_type: Mapped[str | None] = mapped_column(String(30))  # route | top_auto | category
    route_origin: Mapped[str | None] = mapped_column(String(10))
    route_destination: Mapped[str | None] = mapped_column(String(10))
    category_tag: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CampaignSend(Base):
    """Registro individual de envío de una campaña a un usuario."""
    __tablename__ = "campaign_sends"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(Integer, ForeignKey("campaigns.id"), index=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|sent|failed|skipped
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime)
    fcm_response: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CampaignCalendar(Base):
    """
    Calendario de slots de envío de campañas.
    slot_type='weekly': usa day_of_week (0=Lun..6=Dom) + send_hour_ar
    slot_type='special': usa special_date + advance_days (notificar X días antes)
    """
    __tablename__ = "campaign_calendar"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slot_type: Mapped[str] = mapped_column(String(20))              # weekly | special
    day_of_week: Mapped[int | None] = mapped_column(Integer)        # 0=Lun .. 6=Dom
    send_hour_ar: Mapped[int] = mapped_column(Integer, default=10)  # hora en Argentina
    special_date: Mapped[date | None] = mapped_column(Date)
    label: Mapped[str | None] = mapped_column(String(200))
    advance_days: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
