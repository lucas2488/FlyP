from pydantic import BaseModel
from typing import Optional


class UserProfileData(BaseModel):
    userId: str
    fcmToken: str
    appVersion: Optional[str] = None
    deviceModel: Optional[str] = None
    osVersion: Optional[str] = None
    language: Optional[str] = None
    timezone: Optional[str] = None
    selectedCountry: Optional[str] = None
    selectedCurrency: Optional[str] = None
    lastAppOpen: Optional[int] = None
    lastUpdatedAt: int
    lastSearchDate: Optional[int] = None
    lastSearchOrigin: Optional[str] = None
    lastSearchDestination: Optional[str] = None
    lastSearchOriginIata: Optional[str] = None
    lastSearchDestinationIata: Optional[str] = None
    lastSearchOriginGeoId: Optional[str] = None
    lastSearchOriginCountry: Optional[str] = None
    lastSearchDestinationGeoId: Optional[str] = None
    lastSearchDestinationCountry: Optional[str] = None
    lastFlightStatusClickDate: Optional[int] = None
    lastCheckinClickDate: Optional[int] = None
    totalSearches: int = 0
    totalFlightStatusClicks: int = 0
    totalCheckinClicks: int = 0
    # Optional: Skyscanner headers para price monitoring
    skyscannerCurrency: Optional[str] = None
    skyscannerLocale: Optional[str] = None
    skyscannerMarket: Optional[str] = None
    # Segmentación calculada por el Android
    userSegment: Optional[str] = None       # heavy_searcher | casual | inactive | new_user | …
    engagementScore: Optional[float] = None  # 0.0 – 100.0


class WebhookResponse(BaseModel):
    success: bool
    message: Optional[str] = None
    userId: Optional[str] = None


# --- Motor de Oportunidad ---

class DayPriceDTO(BaseModel):
    day: str          # "2026-06-15"
    group: str        # "low" | "medium" | "high"
    price: float


class PriceCalendarRequest(BaseModel):
    fcm_token: str        # identificador del dispositivo
    origin_iata: str
    destination_iata: str
    currency: str
    days: list[DayPriceDTO]


class MonthPriceDTO(BaseModel):
    year: int
    month: int
    price: float
    price_category: str   # PRICE_CATEGORY_LOW / LOWEST / HIGH / UNSPECIFIED


class MonthCalendarRequest(BaseModel):
    origin_iata: str      # sin user — es dato de ruta, no de usuario
    destination_iata: str
    currency: str
    months: list[MonthPriceDTO]


# --- Favoritos ---

class FavoriteRouteDTO(BaseModel):
    origin: str
    destination: str


class SyncFavoritesRequest(BaseModel):
    fcm_token: str
    routes: list[FavoriteRouteDTO]
