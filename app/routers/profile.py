import json
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models import UserProfile, PriceWatch, AirportCache
from app.schemas import UserProfileData, WebhookResponse
from app.services.welcome_service import enqueue_welcome_notification

router = APIRouter()


@router.post("/profile", response_model=WebhookResponse)
async def save_user_profile(data: UserProfileData, db: AsyncSession = Depends(get_db)):
    # Si userId viene null (nunca debería, pero por seguridad) usamos fcmToken como fallback
    user_id = data.userId or data.fcmToken

    result = await db.execute(select(UserProfile).where(UserProfile.user_id == user_id))
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
            user_id=user_id,
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
            user_segment=data.userSegment,
            engagement_score=data.engagementScore,
        )
        db.add(profile)
        # Encolar notificación de bienvenida (se enviará 24h después)
        await enqueue_welcome_notification(db, user_id)
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
        if data.userSegment is not None:
            profile.user_segment = data.userSegment
        if data.engagementScore is not None:
            profile.engagement_score = data.engagementScore

    # Si hay búsqueda, crear/activar price watch + cachear aeropuertos
    if data.lastSearchOriginIata and data.lastSearchDestinationIata:
        await _upsert_price_watch(
            db=db,
            user_id=user_id,
            origin=data.lastSearchOriginIata,
            destination=data.lastSearchDestinationIata,
        )
        await _upsert_airport(
            db=db,
            iata_code=data.lastSearchOriginIata,
            name=data.lastSearchOrigin,
            country=data.lastSearchOriginCountry,
            geo_id=data.lastSearchOriginGeoId,
        )
        await _upsert_airport(
            db=db,
            iata_code=data.lastSearchDestinationIata,
            name=data.lastSearchDestination,
            country=data.lastSearchDestinationCountry,
            geo_id=data.lastSearchDestinationGeoId,
        )

    await db.commit()
    return WebhookResponse(success=True, message="Profile saved", userId=user_id)


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


async def _upsert_airport(
    db: AsyncSession,
    iata_code: str,
    name: str | None = None,
    country: str | None = None,
    geo_id: str | None = None,
    place_id: str | None = None,
):
    """Upsert de aeropuerto. Acumula búsquedas para tener un registro local de todos los airports vistos."""
    result = await db.execute(
        select(AirportCache).where(AirportCache.iata_code == iata_code)
    )
    airport = result.scalar_one_or_none()
    if airport is None:
        db.add(AirportCache(
            iata_code=iata_code,
            name=name,
            country=country,
            geo_id=geo_id,
            place_id=place_id,
            times_searched=1,
        ))
    else:
        airport.times_searched = (airport.times_searched or 0) + 1
        # Actualizar campos si llegaron con más info
        if name and not airport.name:
            airport.name = name
        if country and not airport.country:
            airport.country = country
        if geo_id and not airport.geo_id:
            airport.geo_id = geo_id
        if place_id and not airport.place_id:
            airport.place_id = place_id
