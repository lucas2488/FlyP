import json
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models import UserProfile, PriceWatch
from app.schemas import UserProfileData, WebhookResponse

router = APIRouter()


@router.post("/profile", response_model=WebhookResponse)
async def save_user_profile(data: UserProfileData, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(UserProfile).where(UserProfile.user_id == data.userId))
    profile = result.scalar_one_or_none()

    skyscanner_headers = None
    if data.skyscannerCurrency or data.skyscannerLocale or data.skyscannerMarket:
        skyscanner_headers = json.dumps({
            "X-Skyscanner-Currency": data.skyscannerCurrency,
            "X-Skyscanner-Locale": data.skyscannerLocale,
            "X-Skyscanner-Market": data.skyscannerMarket,
        })

    if profile is None:
        profile = UserProfile(
            user_id=data.userId,
            fcm_token=data.fcmToken,
            app_version=data.appVersion,
            device_model=data.deviceModel,
            os_version=data.osVersion,
            language=data.language,
            timezone=data.timezone,
            selected_country=data.selectedCountry,
            selected_currency=data.selectedCurrency,
            last_app_open=data.lastAppOpen,
            last_updated_at=data.lastUpdatedAt,
            last_search_date=data.lastSearchDate,
            last_search_origin=data.lastSearchOrigin,
            last_search_destination=data.lastSearchDestination,
            last_search_origin_iata=data.lastSearchOriginIata,
            last_search_destination_iata=data.lastSearchDestinationIata,
            last_search_origin_geo_id=data.lastSearchOriginGeoId,
            last_search_origin_country=data.lastSearchOriginCountry,
            last_search_destination_geo_id=data.lastSearchDestinationGeoId,
            last_search_destination_country=data.lastSearchDestinationCountry,
            last_flight_status_click_date=data.lastFlightStatusClickDate,
            last_checkin_click_date=data.lastCheckinClickDate,
            total_searches=data.totalSearches,
            total_flight_status_clicks=data.totalFlightStatusClicks,
            total_checkin_clicks=data.totalCheckinClicks,
            skyscanner_headers=skyscanner_headers,
        )
        db.add(profile)
    else:
        profile.fcm_token = data.fcmToken
        profile.app_version = data.appVersion
        profile.device_model = data.deviceModel
        profile.os_version = data.osVersion
        profile.language = data.language
        profile.timezone = data.timezone
        profile.selected_country = data.selectedCountry
        profile.selected_currency = data.selectedCurrency
        profile.last_app_open = data.lastAppOpen
        profile.last_updated_at = data.lastUpdatedAt
        profile.last_search_date = data.lastSearchDate
        profile.last_search_origin = data.lastSearchOrigin
        profile.last_search_destination = data.lastSearchDestination
        profile.last_search_origin_iata = data.lastSearchOriginIata
        profile.last_search_destination_iata = data.lastSearchDestinationIata
        profile.last_search_origin_geo_id = data.lastSearchOriginGeoId
        profile.last_search_origin_country = data.lastSearchOriginCountry
        profile.last_search_destination_geo_id = data.lastSearchDestinationGeoId
        profile.last_search_destination_country = data.lastSearchDestinationCountry
        profile.last_flight_status_click_date = data.lastFlightStatusClickDate
        profile.last_checkin_click_date = data.lastCheckinClickDate
        profile.total_searches = data.totalSearches
        profile.total_flight_status_clicks = data.totalFlightStatusClicks
        profile.total_checkin_clicks = data.totalCheckinClicks
        if skyscanner_headers:
            profile.skyscanner_headers = skyscanner_headers

    # Si hay búsqueda, crear/activar un price watch para esta ruta
    if data.lastSearchOriginIata and data.lastSearchDestinationIata:
        await _upsert_price_watch(
            db=db,
            user_id=data.userId,
            origin=data.lastSearchOriginIata,
            destination=data.lastSearchDestinationIata,
        )

    await db.commit()
    return WebhookResponse(success=True, message="Profile saved", userId=data.userId)


async def _upsert_price_watch(db: AsyncSession, user_id: str, origin: str, destination: str):
    result = await db.execute(
        select(PriceWatch).where(
            PriceWatch.user_id == user_id,
            PriceWatch.origin == origin,
            PriceWatch.destination == destination,
        )
    )
    watch = result.scalar_one_or_none()
    if watch is None:
        db.add(PriceWatch(user_id=user_id, origin=origin, destination=destination, trip_type="ONE_WAY"))
    else:
        watch.is_active = True
