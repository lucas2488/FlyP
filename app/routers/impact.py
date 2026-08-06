import asyncio

from fastapi import APIRouter, Query, Depends
from pydantic import BaseModel
from sqlalchemy import select, update, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, AsyncSessionLocal
from app.models import ImpactLinkLog, ImpactLink
from app.services import impact_service

router = APIRouter()

_PREWARM_CONCURRENCY = 5


@router.post("/impact")
async def log_impact_url(
    url: str = Query(..., description="Affiliate URL that was opened"),
    user_id: str = Query(None, alias="userId"),
    db: AsyncSession = Depends(get_db),
):
    db.add(ImpactLinkLog(user_id=user_id, url=url))
    await db.commit()
    return {"success": True}


# ---------------------------------------------------------------------------
# Cache/creación de tracking links de Impact — ver DESIGN_impact_links.md
# ---------------------------------------------------------------------------

class PrewarmRequest(BaseModel):
    user_id: str | None = None
    urls: list[str]


class ResolveRequest(BaseModel):
    url: str
    user_id: str | None = None


async def _ensure_link(url: str) -> dict:
    """
    Garantiza que exista el tracking link para `url`. Usa una sesión propia
    (se llama en paralelo desde prewarm). Idempotente: solo el que inserta la
    fila (RETURNING id) llama a Impact; los demás ven el estado actual.
    """
    h = impact_service.url_hash(url)
    async with AsyncSessionLocal() as db:
        claimed = (await db.execute(
            pg_insert(ImpactLink)
            .values(url_hash=h, url=url, status="pending")
            .on_conflict_do_nothing(index_elements=["url_hash"])
            .returning(ImpactLink.id)
        )).scalar_one_or_none()
        await db.commit()

        if claimed is not None:
            impact_url = await impact_service.create_impact_link(url)
            status = "ready" if impact_url else "failed"
            await db.execute(
                update(ImpactLink).where(ImpactLink.url_hash == h)
                .values(impact_url=impact_url, status=status)
            )
            await db.commit()

        row = (await db.execute(
            select(ImpactLink).where(ImpactLink.url_hash == h)
        )).scalar_one_or_none()
        return {
            "url": url,
            "status": row.status if row else "failed",
            "impact_url": row.impact_url if row else None,
        }


@router.post("/impact/prewarm")
async def prewarm(body: PrewarmRequest):
    """
    Pre-crea (si no existen) los tracking links de una lista de deeplinks.
    La app lo llama fire-and-forget al abrir el detalle del vuelo.
    """
    sem = asyncio.Semaphore(_PREWARM_CONCURRENCY)

    async def one(u: str):
        async with sem:
            return await _ensure_link(u)

    results = await asyncio.gather(*(one(u) for u in body.urls))
    return {"results": results}


@router.post("/impact/resolve")
async def resolve(body: ResolveRequest, db: AsyncSession = Depends(get_db)):
    """
    Click: devuelve el tracking link ya creado. Si no está en cache lo crea al
    momento. Si Impact falla, devuelve impact_url=null y la app hace su fallback.
    """
    h = impact_service.url_hash(body.url)

    row = (await db.execute(
        select(ImpactLink).where(ImpactLink.url_hash == h)
    )).scalar_one_or_none()

    # Cache hit
    if row and row.status == "ready" and row.impact_url:
        db.add(ImpactLinkLog(user_id=body.user_id, url=row.impact_url))
        await db.execute(
            update(ImpactLink).where(ImpactLink.url_hash == h)
            .values(use_count=ImpactLink.use_count + 1, last_used_at=func.now())
        )
        await db.commit()
        return {"impact_url": row.impact_url}

    # Cache miss / pending / failed → crear al momento
    impact_url = await impact_service.create_impact_link(body.url)
    if impact_url:
        await db.execute(
            pg_insert(ImpactLink)
            .values(url_hash=h, url=body.url, impact_url=impact_url,
                    status="ready", use_count=1, last_used_at=func.now())
            .on_conflict_do_update(
                index_elements=["url_hash"],
                set_={
                    "impact_url": impact_url,
                    "status": "ready",
                    "use_count": ImpactLink.use_count + 1,
                    "last_used_at": func.now(),
                },
            )
        )
        db.add(ImpactLinkLog(user_id=body.user_id, url=impact_url))
        await db.commit()
        return {"impact_url": impact_url}

    # Impact falló → la app hace fallback (Skyscanner redirect API / directo)
    return {"impact_url": None}
