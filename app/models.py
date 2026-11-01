from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import BigInteger, Boolean, Date, Float, ForeignKey, Integer, Numeric, String, Text, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fcm_token: Mapped[str] = mapped_column(Text, unique=True)   # identidad real del dispositivo
    user_id: Mapped[str | None] = mapped_column(String)         # nullable — reservado para auth futura
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
    last_search_origin_country: Mapped[str | None] = mapped_column(String(100))
    last_search_destination_geo_id: Mapped[str | None] = mapped_column(String(50))
    last_search_destination_country: Mapped[str | None] = mapped_column(String(100))
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
    user_id: Mapped[str | None] = mapped_column(String, index=True)  # almacena fcm_token del dispositivo
    origin: Mapped[str] = mapped_column(String(10))
    destination: Mapped[str] = mapped_column(String(10))
    trip_type: Mapped[str | None] = mapped_column(String(20))
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


class ImpactLink(Base):
    """
    Cache de tracking links de Impact.com por deeplink.
    Se pre-crean al abrir el detalle del vuelo (prewarm) y se reusan en el click
    (resolve), para desacoplar creación de uso. Dedup por hash SHA-256 del deeplink
    (la url es muy larga para un índice único directo).
    """
    __tablename__ = "impact_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # SHA-256 hex
    url: Mapped[str] = mapped_column(Text)                                       # deeplink crudo
    impact_url: Mapped[str | None] = mapped_column(Text)                         # TrackingURL creada
    status: Mapped[str] = mapped_column(String(10), default="pending")           # pending|ready|failed
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)
    use_count: Mapped[int] = mapped_column(Integer, default=0)


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
    country: Mapped[str | None] = mapped_column(String(100))
    geo_id: Mapped[str | None] = mapped_column(String(50))
    place_id: Mapped[str | None] = mapped_column(String(100))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    times_searched: Mapped[int] = mapped_column(Integer, default=1)


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(String, index=True)  # nullable — dato de ruta
    origin: Mapped[str] = mapped_column(String(10))
    destination: Mapped[str] = mapped_column(String(10))
    trip_type: Mapped[str | None] = mapped_column(String(20))
    snapshot_date: Mapped[str] = mapped_column(String(10))  # "2026-06-15"
    price_raw: Mapped[float] = mapped_column(Float)
    price_group: Mapped[str] = mapped_column(String(10))    # "low"
    currency: Mapped[str] = mapped_column(String(3))
    received_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class SearchEvent(Base):
    __tablename__ = "search_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(String, index=True)  # almacena fcm_token del dispositivo
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
    user_id: Mapped[str | None] = mapped_column(String, index=True)  # almacena fcm_token del dispositivo
    origin: Mapped[str] = mapped_column(String(10))
    destination: Mapped[str] = mapped_column(String(10))
    price_raw: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3))
    pct_drop: Mapped[float] = mapped_column(Float)
    reference_price: Mapped[float] = mapped_column(Float)
    trip_type: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|sent|failed|skipped
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    error_msg: Mapped[str | None] = mapped_column(Text)
    drop_level: Mapped[str | None] = mapped_column(String(10))          # soft|strong|urgent|welcome
    notification_type: Mapped[str] = mapped_column(String(20), default="price_drop")
    opened_at: Mapped[datetime | None] = mapped_column(DateTime)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime)     # None = enviar de inmediato


class PriceMonth(Base):
    """
    Precios mensuales por ruta enviados desde fetchMonthPriceCalendar.
    Dato de ruta, no de usuario — upsert por (origin, destination, year, month).
    price_category: PRICE_CATEGORY_LOW | PRICE_CATEGORY_LOWEST | PRICE_CATEGORY_HIGH | PRICE_CATEGORY_UNSPECIFIED
    """
    __tablename__ = "price_months"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(String, index=True)  # nullable — dato de ruta
    origin: Mapped[str] = mapped_column(String(10))
    destination: Mapped[str] = mapped_column(String(10))
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    price_raw: Mapped[float] = mapped_column(Float)
    price_category: Mapped[str] = mapped_column(String(50))
    currency: Mapped[str] = mapped_column(String(3))
    received_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class UserFavorite(Base):
    """
    Rutas favoritas sincronizadas desde el Android (FavoriteSSDao).
    Se hace full-replace en cada POST /api/v1/favorites:
    se eliminan todas las del fcm_token y se insertan las nuevas.

    Identificador real: fcm_token (siempre presente, aunque no haya login).
    user_id: NULL cuando el usuario no está registrado; se rellena cuando existe perfil.
    Nota: usa flyp_user_favorites para evitar colisión con tablas de n8n.
    """
    __tablename__ = "flyp_user_favorites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(String, index=True)      # NULL si no hay login
    fcm_token: Mapped[str | None] = mapped_column(Text, index=True)      # identificador real del dispositivo
    origin_iata: Mapped[str] = mapped_column(String(10))
    destination_iata: Mapped[str] = mapped_column(String(10))
    trip_type: Mapped[str | None] = mapped_column(String(20))
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
    # Mensaje personalizado para feriados/efemérides/fechas especiales.
    # Si está presente → el engine lo usa directamente.
    # Si es None → el engine elige un template genérico (drop_level='campaign').
    custom_title: Mapped[str | None] = mapped_column(String(300))
    custom_body: Mapped[str | None] = mapped_column(Text)
    # Si está seteado → enviar como FCM topic broadcast (1 llamada, todos los suscriptores).
    # Si es None     → envío per-user con ruta personalizada.
    target_topic: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CampaignSend(Base):
    """Registro individual de envío de una campaña a un usuario."""
    __tablename__ = "campaign_sends"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(Integer, ForeignKey("campaigns.id"), index=True)
    user_id: Mapped[str | None] = mapped_column(String, index=True)  # almacena fcm_token del dispositivo
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|sent|failed|skipped
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime)
    fcm_response: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class SpecialDate(Base):
    """
    Fechas especiales para el generador de campañas del mes.
    Distinto de campaign_calendar (que controla cuándo se disparan):
    este modelo describe QUÉ fechas son especiales y sugiere el mensaje.

    tipo: 'feriado' | 'comercial' | 'vacaciones'
    anticipacion_dias: días antes de la fecha en que se debe notificar
    mensaje_sugerido: texto de ayuda para el creador de campañas
    """
    __tablename__ = "special_dates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nombre: Mapped[str] = mapped_column(String(200))
    fecha: Mapped[date] = mapped_column(Date, index=True)
    tipo: Mapped[str] = mapped_column(String(30), default="feriado")  # feriado | comercial | vacaciones
    anticipacion_dias: Mapped[int] = mapped_column(Integer, default=3)
    mensaje_sugerido: Mapped[str | None] = mapped_column(Text)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
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


# ---------------------------------------------------------------------------
# Reporte de afiliados (Impact) — datos sincronizados con el token de solo lectura.
# Flujo: Impact → app/services/impact_sync.py → estas tablas → /api/v1/affiliate/*
# Fechas en timestamptz (UTC); se agrupan en hora de Córdoba al consultar.
# ---------------------------------------------------------------------------

class ImpactProgram(Base):
    __tablename__ = "impact_programs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict] = mapped_column(JSONB)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ImpactAction(Base):
    """Conversión (venta). Upsert por id: cambia de estado y monto con el tiempo."""
    __tablename__ = "impact_actions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    campaign_id: Mapped[str | None] = mapped_column(String(32))
    campaign_name: Mapped[str | None] = mapped_column(Text)
    action_tracker_id: Mapped[str | None] = mapped_column(String(32))
    action_tracker_name: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str | None] = mapped_column(String(16), index=True)
    payout: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    currency: Mapped[str | None] = mapped_column(String(8))
    event_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    locking_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    creation_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cleared_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    referring_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    referring_type: Mapped[str | None] = mapped_column(String(64))
    referring_domain: Mapped[str | None] = mapped_column(Text)
    sub_id1: Mapped[str | None] = mapped_column(Text)
    sub_id2: Mapped[str | None] = mapped_column(Text)
    sub_id3: Mapped[str | None] = mapped_column(Text)
    shared_id: Mapped[str | None] = mapped_column(Text)
    oid: Mapped[str | None] = mapped_column(Text)
    delta_payout: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    delta_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    intended_payout: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    intended_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    ad_id: Mapped[str | None] = mapped_column(String(64))
    promo_code: Mapped[str | None] = mapped_column(Text)
    event_code: Mapped[str | None] = mapped_column(Text)
    customer_city: Mapped[str | None] = mapped_column(Text)
    customer_region: Mapped[str | None] = mapped_column(Text)
    customer_country: Mapped[str | None] = mapped_column(String(64))
    customer_area: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict] = mapped_column(JSONB)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ImpactClick(Base):
    """Click individual (ClickExport). Se reemplazan todos los de un export_date por corrida."""
    __tablename__ = "impact_clicks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    export_date: Mapped[date] = mapped_column(Date, index=True)
    external_id: Mapped[str | None] = mapped_column(String(64))
    event_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    program_id: Mapped[str | None] = mapped_column(String(32))
    program_name: Mapped[str | None] = mapped_column(Text)
    ad_id: Mapped[str | None] = mapped_column(String(64))
    ad_name: Mapped[str | None] = mapped_column(Text)
    ad_type: Mapped[str | None] = mapped_column(String(64))
    product_sku: Mapped[str | None] = mapped_column(Text)
    media_id: Mapped[str | None] = mapped_column(String(64))
    shared_id: Mapped[str | None] = mapped_column(Text)
    sub_id1: Mapped[str | None] = mapped_column(Text)
    sub_id2: Mapped[str | None] = mapped_column(Text)
    sub_id3: Mapped[str | None] = mapped_column(Text)
    customer_country: Mapped[str | None] = mapped_column(String(64))
    customer_region: Mapped[str | None] = mapped_column(Text)
    customer_city: Mapped[str | None] = mapped_column(Text)
    customer_area: Mapped[str | None] = mapped_column(Text)
    ip_hash: Mapped[str | None] = mapped_column(Text)
    device_type: Mapped[str | None] = mapped_column(String(64))
    device_family: Mapped[str | None] = mapped_column(Text)
    browser: Mapped[str | None] = mapped_column(Text)
    os: Mapped[str | None] = mapped_column(Text)
    referring_url: Mapped[str | None] = mapped_column(Text)
    referring_domain: Mapped[str | None] = mapped_column(Text)
    landing_page_url: Mapped[str | None] = mapped_column(Text)
    traffic_source: Mapped[str | None] = mapped_column(Text)
    traffic_category: Mapped[str | None] = mapped_column(Text)
    unique_click: Mapped[bool | None] = mapped_column(Boolean)
    payout: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    deal_name: Mapped[str | None] = mapped_column(Text)
    deal_type: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict] = mapped_column(JSONB)


class ImpactDailyPerformance(Base):
    """Totales por día del reporte de rendimiento (incluye hoy; no gasta ClickExport)."""
    __tablename__ = "impact_daily_performance"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    clicks: Mapped[int | None] = mapped_column(Integer)
    unique_clicks: Mapped[int | None] = mapped_column(Integer)
    actions: Mapped[int | None] = mapped_column(Integer)
    payout: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    sale_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    currency: Mapped[str | None] = mapped_column(String(8))
    raw: Mapped[list] = mapped_column(JSONB)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ImpactInvoice(Base):
    __tablename__ = "impact_invoices"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    currency: Mapped[str | None] = mapped_column(String(8))
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    total_vat_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    status: Mapped[str | None] = mapped_column(String(16))      # PAID | OVERDUE | PENDING (de los LineItems)
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    line_items: Mapped[list] = mapped_column(JSONB)
    detailed_line_items: Mapped[list] = mapped_column(JSONB)
    raw: Mapped[dict] = mapped_column(JSONB)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ImpactClickExport(Base):
    """Estado del ClickExport de cada día (máx. 10 por día en Impact)."""
    __tablename__ = "impact_click_exports"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    status: Mapped[str] = mapped_column(String(16))             # running | completed | failed
    job_id: Mapped[str | None] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    rows: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ImpactSyncRun(Base):
    """Cada corrida de sincronización. De acá sale el 'Actualizado hace X h'."""
    __tablename__ = "impact_sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)   # actions | actions_backfill | performance | ...
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="running")  # running | ok | error | skipped
    rows: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict | None] = mapped_column(JSONB)


# ---------------------------------------------------------------------------
# AdMob — datos sincronizados con el scope admob.readonly.
# Flujo: AdMob API → app/services/admob_sync.py → estas tablas → /api/v1/admob/*
# `date` es el día de AdMob (hora de Los Ángeles). Montos en la moneda de la cuenta,
# ya convertidos de micros. Se guardan métricas base; los ratios se calculan al agregar.
# ---------------------------------------------------------------------------

class AdmobAccount(Base):
    __tablename__ = "admob_account"

    publisher_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    reporting_time_zone: Mapped[str | None] = mapped_column(String(64))
    currency: Mapped[str | None] = mapped_column(String(8))
    raw: Mapped[dict] = mapped_column(JSONB)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AdmobApp(Base):
    __tablename__ = "admob_apps"

    app_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    platform: Mapped[str | None] = mapped_column(String(16))
    name: Mapped[str | None] = mapped_column(Text)
    app_store_id: Mapped[str | None] = mapped_column(Text)
    approval_state: Mapped[str | None] = mapped_column(String(32))
    raw: Mapped[dict] = mapped_column(JSONB)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AdmobAdUnit(Base):
    __tablename__ = "admob_ad_units"

    ad_unit_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    app_id: Mapped[str | None] = mapped_column(String(64), index=True)
    name: Mapped[str | None] = mapped_column(Text)
    format: Mapped[str | None] = mapped_column(String(32))
    ad_types: Mapped[list] = mapped_column(JSONB)
    raw: Mapped[dict] = mapped_column(JSONB)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AdmobDaily(Base):
    """R4: DATE × APP × AD_UNIT × FORMAT × PLATFORM × COUNTRY."""
    __tablename__ = "admob_daily"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    app_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ad_unit_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    format: Mapped[str] = mapped_column(String(32), primary_key=True)
    platform: Mapped[str] = mapped_column(String(16), primary_key=True)
    country: Mapped[str] = mapped_column(String(8), primary_key=True)
    app_name: Mapped[str | None] = mapped_column(Text)
    ad_unit_name: Mapped[str | None] = mapped_column(Text)
    requests: Mapped[int] = mapped_column(BigInteger, default=0)
    matched: Mapped[int] = mapped_column(BigInteger, default=0)
    impressions: Mapped[int] = mapped_column(BigInteger, default=0)
    clicks: Mapped[int] = mapped_column(BigInteger, default=0)
    earnings: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    currency: Mapped[str | None] = mapped_column(String(8))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AdmobDailyVersion(Base):
    """R5–R7: DATE × APP × PLATFORM × (APP_VERSION_NAME | MOBILE_OS_VERSION | GMA_SDK_VERSION)."""
    __tablename__ = "admob_daily_versions"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    dimension_type: Mapped[str] = mapped_column(String(16), primary_key=True)   # app_version | os_version | sdk_version
    app_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    platform: Mapped[str] = mapped_column(String(16), primary_key=True)
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    requests: Mapped[int] = mapped_column(BigInteger, default=0)
    matched: Mapped[int] = mapped_column(BigInteger, default=0)
    impressions: Mapped[int] = mapped_column(BigInteger, default=0)
    clicks: Mapped[int] = mapped_column(BigInteger, default=0)
    earnings: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    currency: Mapped[str | None] = mapped_column(String(8))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AdmobDailyServing(Base):
    """R8: DATE × APP × PLATFORM × FORMAT × SERVING_RESTRICTION."""
    __tablename__ = "admob_daily_serving"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    app_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    platform: Mapped[str] = mapped_column(String(16), primary_key=True)
    format: Mapped[str] = mapped_column(String(32), primary_key=True)
    serving_restriction: Mapped[str] = mapped_column(String(64), primary_key=True)
    requests: Mapped[int] = mapped_column(BigInteger, default=0)
    matched: Mapped[int] = mapped_column(BigInteger, default=0)
    impressions: Mapped[int] = mapped_column(BigInteger, default=0)
    clicks: Mapped[int] = mapped_column(BigInteger, default=0)
    earnings: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    currency: Mapped[str | None] = mapped_column(String(8))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AdmobDailyAdType(Base):
    """R9: DATE × APP × PLATFORM × FORMAT × AD_TYPE (sin solicitudes: incompatibles con AD_TYPE)."""
    __tablename__ = "admob_daily_ad_type"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    app_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    platform: Mapped[str] = mapped_column(String(16), primary_key=True)
    format: Mapped[str] = mapped_column(String(32), primary_key=True)
    ad_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    impressions: Mapped[int] = mapped_column(BigInteger, default=0)
    clicks: Mapped[int] = mapped_column(BigInteger, default=0)
    earnings: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    currency: Mapped[str | None] = mapped_column(String(8))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AdmobMediationDaily(Base):
    """R10: DATE × APP × AD_SOURCE × MEDIATION_GROUP × FORMAT × PLATFORM."""
    __tablename__ = "admob_mediation_daily"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    app_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ad_source_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    mediation_group_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    format: Mapped[str] = mapped_column(String(32), primary_key=True)
    platform: Mapped[str] = mapped_column(String(16), primary_key=True)
    ad_source_name: Mapped[str | None] = mapped_column(Text)
    mediation_group_name: Mapped[str | None] = mapped_column(Text)
    requests: Mapped[int] = mapped_column(BigInteger, default=0)
    matched: Mapped[int] = mapped_column(BigInteger, default=0)
    impressions: Mapped[int] = mapped_column(BigInteger, default=0)
    clicks: Mapped[int] = mapped_column(BigInteger, default=0)
    earnings: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    observed_ecpm: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    ad_load_latency: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(8))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AdmobIntradaySnapshot(Base):
    """Foto de cada corrida horaria: totales del día de AdMob por app (para 'ayer a la misma hora')."""
    __tablename__ = "admob_intraday_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    report_date: Mapped[date] = mapped_column(Date, index=True)
    app_id: Mapped[str] = mapped_column(String(64))
    platform: Mapped[str | None] = mapped_column(String(16))
    earnings: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    impressions: Mapped[int] = mapped_column(BigInteger, default=0)
    clicks: Mapped[int] = mapped_column(BigInteger, default=0)


class AdmobSyncRun(Base):
    __tablename__ = "admob_sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)   # hourly | daily | initial | inventory
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="running")  # running | ok | error | auth_error
    range_from: Mapped[date | None] = mapped_column(Date)
    range_to: Mapped[date | None] = mapped_column(Date)
    rows: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict | None] = mapped_column(JSONB)


# ---------------------------------------------------------------------------
# Alertas de precio (Firestore users/{uid}/alerts): historial de lo que reporta el buscador
# (POST /checker/results) y de los avisos enviados. Firestore guarda el estado actual
# (lastPrice, lowestPrice, lastCheckedAt, lastNotifiedAt); acá queda la historia.
# ---------------------------------------------------------------------------

class PriceAlertCheck(Base):
    """Un precio reportado por el buscador para una búsqueda (search_key)."""
    __tablename__ = "price_alert_checks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    search_key: Mapped[str] = mapped_column(String(32), index=True)
    worker_id: Mapped[str | None] = mapped_column(String(64))
    price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    currency: Mapped[str | None] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(24))      # ok | error | unknown_search | currency_mismatch | invalid_price
    error: Mapped[str | None] = mapped_column(Text)
    alerts_updated: Mapped[int] = mapped_column(Integer, default=0)
    alerts_notified: Mapped[int] = mapped_column(Integer, default=0)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class PriceAlertNotification(Base):
    """Un aviso de bajada creado en users/{uid}/notifications y su push."""
    __tablename__ = "price_alert_notifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    alert_path: Mapped[str] = mapped_column(String(160), index=True)    # users/{uid}/alerts/{id}
    notice_id: Mapped[str] = mapped_column(String(64))
    search_key: Mapped[str] = mapped_column(String(32))
    price: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    previous_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    target_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    currency: Mapped[str | None] = mapped_column(String(8))
    reason: Mapped[str] = mapped_column(String(24))      # target | drop
    devices: Mapped[int] = mapped_column(Integer, default=0)
    pushes_sent: Mapped[int] = mapped_column(Integer, default=0)
    devices_removed: Mapped[int] = mapped_column(Integer, default=0)
    push_errors: Mapped[list | None] = mapped_column(JSONB)
    title: Mapped[str | None] = mapped_column(Text)            # texto del push (y del aviso si es de Claude)
    body: Mapped[str | None] = mapped_column(Text)
    copy_source: Mapped[str | None] = mapped_column(String(16))  # llm | template
    uid: Mapped[str | None] = mapped_column(String(128))
    route: Mapped[str | None] = mapped_column(String(160))      # "Buenos Aires (BUE) → Miami (MIA)", foto al avisar
    country: Mapped[str | None] = mapped_column(String(8))       # mercado de la alerta
    push_status: Mapped[str | None] = mapped_column(String(16), index=True)  # sent | scheduled | cancelled | failed
    push_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))   # horario de silencio
    push_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
