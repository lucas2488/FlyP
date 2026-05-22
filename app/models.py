from datetime import datetime
from sqlalchemy import BigInteger, Boolean, Float, Integer, String, Text, DateTime, func
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
