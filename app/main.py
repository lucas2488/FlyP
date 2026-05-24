import logging
import sys
from contextlib import asynccontextmanager

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:     %(name)s - %(message)s",
    stream=sys.stdout,
)

import firebase_admin
from firebase_admin import credentials
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import engine, Base
from app.config import settings
from app.routers import profile, impact, events, analytics, notifications, favorites, admin, campaigns, internal
from app.services.notification_dispatcher import process_notification_queue
from app.services.reengagement_service import process_reengagement_queue
from app.services.segment_service import recalculate_segments
from app.services.campaign_scheduler import check_scheduled_campaigns
from app.services.welcome_service import process_welcome_notifications

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Crear tablas que no existan (safety net para instalaciones nuevas).
    # Las migraciones de esquema las maneja Alembic (corre antes del startup vía Dockerfile).
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("DB ready")

    # 3. Firebase Admin SDK
    if settings.firebase_credentials_path:
        try:
            cred = credentials.Certificate(settings.firebase_credentials_path)
            firebase_admin.initialize_app(cred)
            logger.info("Firebase Admin SDK initialized")
        except Exception as e:
            logger.warning(f"Firebase init failed (notifications disabled): {e}")
    else:
        logger.warning("FIREBASE_CREDENTIALS_PATH not set — push notifications disabled")

    # 4. APScheduler
    scheduler.add_job(
        process_notification_queue,
        "interval",
        minutes=30,
        id="notification_dispatcher",
        replace_existing=True,
    )
    scheduler.add_job(
        process_reengagement_queue,
        "interval",
        minutes=settings.reengagement_check_interval_minutes,
        id="reengagement_dispatcher",
        replace_existing=True,
    )
    # Recálculo de segmentos — diario a las 3am Argentina
    scheduler.add_job(
        recalculate_segments,
        "cron",
        hour=3,
        minute=0,
        timezone="America/Argentina/Buenos_Aires",
        id="segment_recalculator",
        replace_existing=True,
    )
    # Dispatcher de campañas automáticas — cada hora
    scheduler.add_job(
        check_scheduled_campaigns,
        "interval",
        hours=1,
        id="campaign_scheduler",
        replace_existing=True,
    )
    # Welcome notifications — procesa las que cumplieron 24h de delay
    scheduler.add_job(
        process_welcome_notifications,
        "interval",
        hours=1,
        id="welcome_dispatcher",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        f"APScheduler started — notification dispatcher every 30 min, "
        f"reengagement dispatcher every {settings.reengagement_check_interval_minutes} min, "
        f"segment recalculator daily 3am AR, "
        f"campaign scheduler every 1h, "
        f"welcome dispatcher every 1h"
    )

    yield

    scheduler.shutdown()
    logger.info("APScheduler stopped")


app = FastAPI(title="FlyPromociones Backend", version="1.0.0", lifespan=lifespan)

# CORS — permite requests desde el dashboard de marketing
cors_origins = (
    ["*"] if settings.cors_origins == "*"
    else [o.strip() for o in settings.cors_origins.split(",")]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(profile.router, prefix="/api/v1")
app.include_router(impact.router, prefix="/api/v1")
app.include_router(events.router, prefix="/api/v1")
app.include_router(analytics.router, prefix="/api/v1")
app.include_router(notifications.router, prefix="/api/v1")
app.include_router(favorites.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")
app.include_router(campaigns.router, prefix="/api/v1")
app.include_router(internal.router, prefix="/api/v1")


@app.get("/api/v1/health")
async def health():
    return {"status": "ok", "service": "fly-backend"}
