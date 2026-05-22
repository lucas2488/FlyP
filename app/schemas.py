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


class WebhookResponse(BaseModel):
    success: bool
    message: Optional[str] = None
    userId: Optional[str] = None
