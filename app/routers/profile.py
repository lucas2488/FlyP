import json

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import get_db
from app.models import UserProfile, PriceWatch, AirportCache
from app.schemas import UserProfileData, WebhookResponse
from app.services.welcome_service import enqueue_welcome_notification

router = APIRouter()


@router.post("/profile", response_model=WebhookResponse)
async def save_user_profile(data: UserProfileData, db: AsyncSession = Depends(get_db)):
    fcm_token = data.fcmToken
    if not fcm_token:
        return WebhookResponse(success=False, message="Missing FCM token", userId=None)

    result = await db.execute(select(UserProfile).where(UserProfile.fcm_token == fcm_token))
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
            fcm_token=fcm_token,
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
        await enqueue_welcome_notification(db, fcm_token)
    else:
        profile.fcm_token = fcm_token
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

    if data.lastSearchOriginIata and data.lastSearchDestinationIata:
        await _upsert_price_watch(db, fcm_token, data.lastSearchOriginIata, data.lastSearchDestinationIata)
        # Upsert de aeropuertos en orden determinístico (por IATA) para que todas
        # las transacciones tomen los locks en el mismo orden → sin deadlocks entre
        # requests que comparten aeropuertos populares (AEP, BUEA, etc.).
        airports = sorted(
            [
                (data.lastSearchOriginIata, data.lastSearchOrigin,
                 data.lastSearchOriginCountry, data.lastSearchOriginGeoId),
                (data.lastSearchDestinationIata, data.lastSearchDestination,
                 data.lastSearchDestinationCountry, data.lastSearchDestinationGeoId),
            ],
            key=lambda a: a[0],
        )
        for iata, name, country, geo_id in airports:
            await _upsert_airport(db, iata, name, country, geo_id)

    await db.commit()
    return WebhookResponse(success=True, message="Profile saved", userId=None)


async def _upsert_price_watch(db: AsyncSession, fcm_token: str, origin: str, destination: str):
    result = await db.execute(
        select(PriceWatch).where(
            PriceWatch.user_id == fcm_token,
            PriceWatch.origin == origin,
            PriceWatch.destination == destination,
        )
    )
    watch = result.scalars().first()
    if watch is None:
        db.add(PriceWatch(user_id=fcm_token, origin=origin, destination=destination, trip_type="ONE_WAY"))
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
    # Upsert atómico en una sola sentencia (INSERT ... ON CONFLICT). Evita el
    # patrón SELECT-luego-UPDATE, que alargaba el lock y perdía incrementos bajo
    # concurrencia. times_searched se incrementa en la DB de forma atómica.
    insert_stmt = pg_insert(AirportCache).values(
        iata_code=iata_code, name=name, country=country,
        geo_id=geo_id, place_id=place_id, times_searched=1,
    )
    upsert = insert_stmt.on_conflict_do_update(
        index_elements=["iata_code"],
        set_={
            "times_searched": AirportCache.times_searched + 1,
            "last_seen_at":   func.now(),
            # Completar solo si el campo estaba vacío (no pisar datos existentes).
            "name":     func.coalesce(AirportCache.name,     insert_stmt.excluded.name),
            "country":  func.coalesce(AirportCache.country,  insert_stmt.excluded.country),
            "geo_id":   func.coalesce(AirportCache.geo_id,   insert_stmt.excluded.geo_id),
            "place_id": func.coalesce(AirportCache.place_id, insert_stmt.excluded.place_id),
        },
    )
    await db.execute(upsert)
