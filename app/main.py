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
from sqlalchemy import text
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import engine, Base
from app.config import settings
from app.routers import profile, impact, events, analytics
from app.services.notification_dispatcher import process_notification_queue

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Crear tablas nuevas (no toca las existentes)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # 2. Migrar columnas nuevas en price_watches (idempotente)
        migrations = [
            "ALTER TABLE price_watches ADD COLUMN IF NOT EXISTS last_search_best_price FLOAT",
            "ALTER TABLE price_watches ADD COLUMN IF NOT EXISTS last_selected_price FLOAT",
            "ALTER TABLE price_watches ADD COLUMN IF NOT EXISTS last_notified_at TIMESTAMP WITH TIME ZONE",
            "ALTER TABLE price_watches ADD COLUMN IF NOT EXISTS interest_score INTEGER DEFAULT 0",
            "ALTER TABLE price_watches ADD COLUMN IF NOT EXISTS notification_count INTEGER DEFAULT 0",
        ]
        for migration in migrations:
            await conn.execute(text(migration))
        logger.info("DB migrations applied")

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
    scheduler.start()
    logger.info("APScheduler started (notification dispatcher every 30 min)")

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


@app.get("/api/v1/health")
async def health():
    return {"status": "ok", "service": "fly-backend"}
