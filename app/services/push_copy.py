"""
Texto de los avisos de bajada de precio, escrito por Claude.

Idioma y jerga: según el PAÍS de la alerta (el mercado que eligió el usuario).
Moneda: la de la ALERTA, tal cual (un argentino puede buscar en USD: el texto suena
argentino y el precio va en USD).

Si la API de Anthropic no está configurada, tarda o falla, se devuelve None y el que
llama usa la plantilla fija: el aviso nunca deja de salir por esto.
"""
import logging

import anthropic
from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-5-5"   # texto corto y localizado: no hace falta un modelo más grande
TIMEOUT_SECONDS = 20.0

# Cómo habla cada mercado. Lo que no esté acá usa el idioma principal del país.
VOICE = {
    "AR": "español rioplatense de Argentina: voseo (aprovechá, mirá, tocá), expresiones como 'che', 're barato', 'una ganga', 'volá'",
    "UY": "español rioplatense de Uruguay: voseo, tono cercano ('bo', 'tremendo precio')",
    "CL": "español de Chile, cercano y con chilenismos suaves ('bacán', 'aprovecha al tiro')",
    "CO": "español de Colombia: tuteo o usted cálido, expresiones como 'qué chévere', 'una ganga', 'no te lo pierdas, parce' con moderación",
    "MX": "español de México: tuteo, expresiones como 'qué padre', 'está bien barato', 'no te lo pierdas'",
    "PE": "español de Perú: tuteo cercano ('chévere', 'aprovecha')",
    "ES": "español de España: tuteo, expresiones como 'chollo', 'menudo precio', 'no te lo pierdas', 'vuela'",
    "BR": "português do Brasil, informal e animado ('baixou!', 'corre', 'imperdível', 'bora viajar')",
    "PT": "português europeu, tom próximo",
    "US": "American English, upbeat and casual ('deal alert', 'grab it', 'don't miss out')",
    "GB": "British English, friendly ('cracking deal', 'bargain')",
}

SYSTEM = """Escribís notificaciones push para Fly Promociones, una app que avisa cuando baja el precio de un vuelo que el usuario está siguiendo.

Reglas:
- Escribí en el idioma y con la jerga del mercado que te indiquen. Que suene natural, como lo diría alguien de ahí; nada de traducciones literales.
- Siempre con emojis: 1 o 2 en el título y 1 a 3 en el texto. Que tengan que ver con viajes, precios o entusiasmo.
- Título: máximo 50 caracteres. Tiene que decir que bajó el precio (o que llegó al precio que el usuario quería) y mencionar el destino.
- Texto: máximo 140 caracteres. Tiene que incluir el precio nuevo EXACTAMENTE como te lo paso (moneda incluida) y, si te paso el precio anterior y el porcentaje, también. Cerrá invitando a tocar para ver el vuelo.
- La moneda es la que eligió el usuario: no la conviertas ni la cambies aunque no sea la del país.
- No inventes datos: ni aerolíneas, ni fechas, ni precios que no te pasé. No prometas que el precio va a durar.
- Sin comillas alrededor del texto, sin hashtags."""


class PushCopy(BaseModel):
    title: str = Field(description="Título del push: máximo 50 caracteres, con 1 o 2 emojis")
    body: str = Field(description="Texto del push: máximo 140 caracteres, con 1 a 3 emojis")


_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic | None:
    global _client
    if not settings.anthropic_api_key:
        return None
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key, timeout=TIMEOUT_SECONDS, max_retries=1)
    return _client


def build_request(*, country: str, origin: str, destination: str, trip: str, price_txt: str,
                  previous_txt: str | None, pct: int | None, reason: str) -> str:
    voice = VOICE.get(country.upper(), f"el idioma principal de {country} (código de país ISO), con tono cercano")
    lines = [
        f"Mercado: {country.upper()} → escribí en {voice}.",
        f"Ruta: {origin} → {destination} ({trip}).",
        f"Precio nuevo: {price_txt}.",
    ]
    if previous_txt:
        lines.append(f"Precio anterior (cuando creó la alerta): {previous_txt}.")
    if pct:
        lines.append(f"Bajó: {pct} %.")
    lines.append("Motivo: llegó al precio objetivo que puso el usuario." if reason == "target"
                 else "Motivo: el precio bajó respecto de cuando creó la alerta.")
    return "\n".join(lines)


async def generate(**kw) -> PushCopy | None:
    """Título y texto del push, o None si no se pudo (se usa la plantilla)."""
    client = _get_client()
    if client is None:
        return None
    try:
        r = await client.messages.parse(
            model=MODEL,
            max_tokens=1000,
            output_config={"effort": "low"},     # texto corto: no hace falta pensar mucho
            system=SYSTEM,
            messages=[{"role": "user", "content": build_request(**kw)}],
            output_format=PushCopy,
        )
    except anthropic.APIError as e:
        logger.warning("push_copy: Anthropic falló (%s), uso la plantilla", e)
        return None
    if r.stop_reason == "refusal" or r.parsed_output is None:
        logger.warning("push_copy: sin texto (stop_reason=%s), uso la plantilla", r.stop_reason)
        return None
    out = r.parsed_output
    if not out.title.strip() or not out.body.strip() or len(out.title) > 70 or len(out.body) > 180:
        logger.warning("push_copy: texto fuera de rango, uso la plantilla")
        return None
    return out
