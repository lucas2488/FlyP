"""
Admin router — gestión de notification_templates.

Todos los endpoints requieren X-API-Key (mismo key que analytics).

Endpoints:
  GET    /api/v1/admin/templates                  — listar (filtros: country, level, active_only)
  POST   /api/v1/admin/templates                  — crear uno manualmente
  POST   /api/v1/admin/templates/bulk             — crear varios (usado tras /generate)
  PATCH  /api/v1/admin/templates/{id}/toggle      — activar / desactivar
  DELETE /api/v1/admin/templates/{id}             — eliminar definitivamente
  POST   /api/v1/admin/templates/generate         — generar con IA (preview, no guarda)
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
import sqlalchemy as sa
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import NotificationTemplate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Jerga por país — incluida en el prompt de generación
# Para países no listados acá se usa el fallback "*"
_COUNTRY_CONTEXT: dict[str, str] = {
    "AR": (
        "Argentina. Jerga rioplatense: che, fiera, piola, copado/copada, chabón/chabona, "
        "re (prefijo de énfasis), joya, golazo, posta (en serio), arrancá (empezá), "
        "capo/capa, una masa (genial), quilombo (lío), boludo (coloquial afectuoso), "
        "mangos (plata), facha, se armó, no aflojes, para el chori."
    ),
    "MX": (
        "México. Jerga mexicana: órale, no manches, chido/chida, güey/wey, a huevo (sí), "
        "neta (la verdad), cañón (intenso/difícil), dale gas, aguas (cuidado), de pelos, "
        "ahorita, sale (de acuerdo), cuate, está heavy, qué onda, no te rajes."
    ),
    "CO": (
        "Colombia. Jerga colombiana: parce, bacano, chimba (genial), berraco/berraca (fuerte/audaz), "
        "qué nota, broder, pilas (ojo/cuidado), de una (de inmediato), pues (muletilla), "
        "tiquete (boleto de avión), no hay tos (no hay problema)."
    ),
    "CL": (
        "Chile. Jerga chilena: po (partícula final de énfasis), cachai (entendés/sabés), "
        "al tiro (de inmediato), wena (bueno/ok), filete (excelente), bacán, "
        "la raja (lo mejor), caleta (bastante), weón/huevón (amigo coloquial)."
    ),
    "ES": (
        "España. Jerga española: tío/tía (amigo), mola (es cool), guay (genial), "
        "flipar (alucinar), cojonudo (genial), ostras (vaya), madre mía, "
        "qué pasada, chulo/chula, tiene tela (es increíble)."
    ),
    "BR": (
        "Brasil. IMPORTANTE: escribir en PORTUGUÉS BRASILEIRO (no español). "
        "Jerga brasileña: cara/mano/véi (amigo), partiu/bora (vamos), topzera/top (genial), "
        "demais (increíble), que massa/show de bola (qué bueno), "
        "rapaz (chico), irmão (hermano), aproveita (aprovecha), não perde (no pierdas), "
        "manda ver (manda ver / dale)."
    ),
    "UY": (
        "Uruguay. Español rioplatense con modismos uruguayos: che, botija (joven/amigo), "
        "ta (está bien/ok), de taquito (fácil/sin esfuerzo), piola, copado, "
        "de una (de inmediato)."
    ),
    "PY": (
        "Paraguay. Español con influencia del guaraní y modismos locales: "
        "compañero, al palo (genial/rápido), ta bien (está bien), che."
    ),
    "PE": (
        "Perú. Jerga peruana: causa/pata (amigo), al toque (de inmediato), "
        "chévere (genial), bacán, qué bacanería (qué bueno), pe/pues (muletilla), "
        "habla (oye/hola), de una."
    ),
    "VE": (
        "Venezuela. Jerga venezolana: chamo/chama (joven), pana (amigo), "
        "chévere (genial), burda (mucho/muy), no joda (no manches), "
        "qué vaina (qué cosa), bacán, dale."
    ),
    "*": (
        "Español neutro latinoamericano, sin jerga regional específica. "
        "Tono amigable y entusiasta."
    ),
}

# Países de habla no-española — NO caen a AR como fallback
_NON_SPANISH_COUNTRIES: frozenset[str] = frozenset({"BR"})

_LEVEL_CONTEXT: dict[str, str] = {
    "soft":         "bajada pequeña (~5%). Tono: optimista, invita a aprovechar, sin urgencia.",
    "strong":       "bajada moderada (~10%). Tono: entusiasta, destacar la oportunidad.",
    "urgent":       "bajada grande (~15%+). Tono: urgente, FOMO, 'es ahora o nunca'.",
    "reengagement": (
        "recordatorio post-búsqueda (el usuario buscó hace ~20 min y no compró). "
        "Tono: cálido, recordatorio gentil. Variables disponibles: {destination}, {price}, {currency}. "
        "IMPORTANTE: en estos templates NO uses {origin}, {pct} — no están disponibles."
    ),
}


async def verify_api_key(api_key: str = Security(api_key_header)) -> str:
    if not api_key or api_key != settings.analytics_api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return api_key


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class TemplateCreate(BaseModel):
    country_code: str = Field(..., max_length=10)   # sin validación de lista — acepta cualquier país
    drop_level: str = Field(..., pattern="^(soft|strong|urgent|reengagement)$")
    title_template: str = Field(..., min_length=5)
    body_template: str = Field(..., min_length=5)


class TemplateBulkCreate(BaseModel):
    templates: list[TemplateCreate]


class GenerateRequest(BaseModel):
    country_code: str = Field(..., max_length=10)   # acepta cualquier código ISO
    drop_level: str = Field(..., pattern="^(soft|strong|urgent|reengagement)$")
    count: int = Field(default=5, ge=1, le=20)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _template_to_dict(t: NotificationTemplate) -> dict:
    return {
        "id":             t.id,
        "country_code":   t.country_code,
        "drop_level":     t.drop_level,
        "title_template": t.title_template,
        "body_template":  t.body_template,
        "is_active":      t.is_active,
        "created_at":     t.created_at.isoformat() if t.created_at else None,
    }


# ---------------------------------------------------------------------------
# GET /admin/templates/countries  — lista dinámica de países con conteo
# ---------------------------------------------------------------------------

@router.get("/templates/countries")
async def list_countries(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
) -> list[dict]:
    """
    Devuelve los países que tienen al menos un template activo, con su conteo.
    El dashboard lo usa para renderizar los pills de filtro dinámicamente.
    """
    from sqlalchemy import func as sqlfunc
    result = await db.execute(
        select(
            NotificationTemplate.country_code,
            sqlfunc.count(NotificationTemplate.id).label("total"),
            sqlfunc.sum(
                sqlfunc.cast(NotificationTemplate.is_active, sa.Integer)
            ).label("active"),
        )
        .group_by(NotificationTemplate.country_code)
        .order_by(NotificationTemplate.country_code)
    )
    rows = result.all()
    return [
        {
            "code":   r.country_code,
            "total":  r.total,
            "active": int(r.active or 0),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# GET /admin/templates
# ---------------------------------------------------------------------------

@router.get("/templates")
async def list_templates(
    country_code: str | None = None,
    drop_level: str | None = None,
    active_only: bool = False,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
) -> list[dict]:
    q = select(NotificationTemplate).order_by(
        NotificationTemplate.country_code,
        NotificationTemplate.drop_level,
        NotificationTemplate.id,
    )
    if country_code:
        q = q.where(NotificationTemplate.country_code == country_code)
    if drop_level:
        q = q.where(NotificationTemplate.drop_level == drop_level)
    if active_only:
        q = q.where(NotificationTemplate.is_active.is_(True))

    result = await db.execute(q)
    return [_template_to_dict(t) for t in result.scalars().all()]


# ---------------------------------------------------------------------------
# POST /admin/templates
# ---------------------------------------------------------------------------

@router.post("/templates", status_code=201)
async def create_template(
    body: TemplateCreate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
) -> dict:
    t = NotificationTemplate(
        country_code=body.country_code,
        drop_level=body.drop_level,
        title_template=body.title_template,
        body_template=body.body_template,
        is_active=True,
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return _template_to_dict(t)


# ---------------------------------------------------------------------------
# POST /admin/templates/bulk
# ---------------------------------------------------------------------------

@router.post("/templates/bulk", status_code=201)
async def bulk_create_templates(
    body: TemplateBulkCreate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
) -> dict:
    created = []
    for item in body.templates:
        t = NotificationTemplate(
            country_code=item.country_code,
            drop_level=item.drop_level,
            title_template=item.title_template,
            body_template=item.body_template,
            is_active=True,
        )
        db.add(t)
        created.append(t)

    await db.commit()
    for t in created:
        await db.refresh(t)

    return {"created": len(created), "templates": [_template_to_dict(t) for t in created]}


# ---------------------------------------------------------------------------
# PATCH /admin/templates/{id}/toggle
# ---------------------------------------------------------------------------

@router.patch("/templates/{template_id}/toggle")
async def toggle_template(
    template_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
) -> dict:
    t = await db.get(NotificationTemplate, template_id)
    if not t:
        raise HTTPException(404, "template_not_found")
    t.is_active = not t.is_active
    await db.commit()
    await db.refresh(t)
    return _template_to_dict(t)


# ---------------------------------------------------------------------------
# DELETE /admin/templates/{id}
# ---------------------------------------------------------------------------

@router.delete("/templates/{template_id}")
async def delete_template(
    template_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_api_key),
) -> dict:
    t = await db.get(NotificationTemplate, template_id)
    if not t:
        raise HTTPException(404, "template_not_found")
    await db.execute(
        delete(NotificationTemplate).where(NotificationTemplate.id == template_id)
    )
    await db.commit()
    return {"success": True, "deleted_id": template_id}


# ---------------------------------------------------------------------------
# POST /admin/templates/generate
# Genera templates con OpenAI (preview — no los guarda automáticamente)
# ---------------------------------------------------------------------------

@router.post("/templates/generate")
async def generate_templates(
    body: GenerateRequest,
    _: str = Depends(verify_api_key),
) -> dict:
    if not settings.openai_api_key:
        raise HTTPException(503, "OpenAI API key not configured (set OPENAI_API_KEY in .env)")

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=settings.openai_api_key)
    except ImportError:
        raise HTTPException(503, "openai package not installed")

    country_ctx = _COUNTRY_CONTEXT.get(body.country_code, _COUNTRY_CONTEXT["*"])
    level_ctx   = _LEVEL_CONTEXT.get(body.drop_level, "bajada de precio")

    is_reengagement = body.drop_level == "reengagement"
    variables_note = (
        "Variables disponibles (usar EXACTAMENTE como aparecen, con llaves): "
        "{destination}, {price}, {currency}"
        if is_reengagement else
        "Variables disponibles (usar EXACTAMENTE como aparecen, con llaves): "
        "{origin}, {destination}, {pct}, {price}, {currency}"
    )

    system_prompt = (
        "Sos un experto en copywriting de notificaciones push para apps de vuelos. "
        "Escribís mensajes cortos, directos, con jerga local y emojis. "
        "Nunca usás formalidades ni gerundios largos. "
        "Respondés SOLO con un JSON válido, sin texto adicional."
    )

    user_prompt = f"""Generá {body.count} templates de notificación push para la app FlyPromociones.

País: {body.country_code} — {country_ctx}

Nivel: {body.drop_level} — {level_ctx}

{variables_note}

Reglas:
- title: máximo 60 caracteres. Puede incluir emojis. Debe mencionar la ruta o el destino.
- body: máximo 100 caracteres. Clara, con jerga del país y un CTA implícito.
- Cada template debe ser DIFERENTE al resto. Variá la estructura, los emojis y la jerga.
- NO repitas templates que ya existan. Sé creativo.

Respondé ÚNICAMENTE con este JSON (sin markdown, sin texto extra):
[
  {{"title": "...", "body": "..."}},
  ...
]"""

    try:
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.9,
            max_tokens=1500,
        )
        raw = response.choices[0].message.content or ""

        # Limpiar posible markdown code fence
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        generated = json.loads(raw)

        # Validar estructura básica
        templates = []
        for item in generated:
            if isinstance(item, dict) and "title" in item and "body" in item:
                templates.append({
                    "country_code":   body.country_code,
                    "drop_level":     body.drop_level,
                    "title_template": str(item["title"])[:200],
                    "body_template":  str(item["body"])[:400],
                })

        return {"generated": len(templates), "templates": templates}

    except json.JSONDecodeError as e:
        logger.error(f"OpenAI returned invalid JSON: {e}\nRaw: {raw[:500]}")
        raise HTTPException(502, "AI returned invalid JSON — try again")
    except Exception as e:
        logger.error(f"OpenAI generation error: {e}")
        raise HTTPException(502, f"AI generation failed: {str(e)[:200]}")
