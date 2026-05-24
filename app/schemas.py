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


class PriceSnapshotRequest(BaseModel):
    user_id: str
    origin: str
    destination: str
    trip_type: str
    currency: str
    prices: list[DayPriceDTO]


class SearchResultEvent(BaseModel):
    user_id: str
    origin: str
    destination: str
    trip_type: Optional[str] = "ONE_WAY"
    best_price: float
    currency: str


class FlightSelectedEvent(BaseModel):
    user_id: str
    origin: str
    destination: str
    price: float
    currency: str
    trip_type: Optional[str] = "ONE_WAY"
    airline: Optional[str] = None
    departure_date: Optional[str] = None


# --- Favoritos ---

class FavoriteRouteDTO(BaseModel):
    origin: str
    destination: str


class SyncFavoritesRequest(BaseModel):
    user_id: str
    routes: list[FavoriteRouteDTO]
