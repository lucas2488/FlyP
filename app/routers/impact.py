from fastapi import APIRouter, Query, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models import ImpactLinkLog

router = APIRouter()


@router.post("/impact")
async def log_impact_url(
    url: str = Query(..., description="Affiliate URL that was opened"),
    user_id: str = Query(None, alias="userId"),
    db: AsyncSession = Depends(get_db),
):
    db.add(ImpactLinkLog(user_id=user_id, url=url))
    await db.commit()
    return {"success": True}
