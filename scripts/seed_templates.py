#!/usr/bin/env python3
"""
Seed script para notification_templates.

Uso:
    python scripts/seed_templates.py

Opciones:
    --clear    Marca todos los templates existentes como inactivos antes de insertar los nuevos.
               Útil para reemplazar la librería completa sin borrar registros históricos.

Nota: La migración 0004 ya corre este seed automáticamente en el primer deploy.
      Este script existe para actualizaciones futuras de la librería de templates
      sin necesidad de una nueva migración de Alembic.
"""
import argparse
import asyncio
import sys
from pathlib import Path

# Asegurar que el root del proyecto esté en el path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import update
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.config import settings
from app.models import NotificationTemplate

# ---------------------------------------------------------------------------
# Librería de templates — mantener sincronizada con 0004_notification_templates.py
# ---------------------------------------------------------------------------
TEMPLATES = [

    # =========================================================
    # ARGENTINA  (AR)  — 33 templates
    # =========================================================
    ("AR", "soft",  "Che, {origin} → {destination} bajó {pct}% ✈️",                "Hoy salís desde {price} {currency}. ¡Está piola!"),
    ("AR", "soft",  "Fiera, apareció una oferta: {origin} → {destination}",         "Bajó {pct}%. Desde {price} {currency}, una masa."),
    ("AR", "soft",  "✈️ {origin} → {destination} se puso copado",                   "Bajó {pct}%, hoy desde {price} {currency}. Dale que conviene."),
    ("AR", "soft",  "Bajó {pct}% el vuelo a {destination} 🛫",                      "Sale desde {price} {currency}. Re joya para volar."),
    ("AR", "soft",  "Ojo, {origin} → {destination} está más barato",                "Cayó {pct}%, desde {price} {currency}. Posta."),
    ("AR", "soft",  "Che, ¡el vuelo a {destination} bajó!",                         "{origin} → {destination}: -{pct}%, desde {price} {currency}. ¡Un golazo!"),
    ("AR", "soft",  "🎯 Oportunidad: {origin} → {destination}",                     "Bajó {pct}%. Salís desde {price} {currency}. Capo."),
    ("AR", "soft",  "{origin} → {destination} a precio piola 🛫",                   "Desde {price} {currency} nomás. Cayó {pct}%."),
    ("AR", "soft",  "Buena onda en {origin} → {destination} ✨",                    "El precio se fue pa' abajo {pct}%. Desde {price} {currency}."),
    ("AR", "soft",  "Dale che, el vuelo a {destination} bajó 🤩",                   "Hoy desde {price} {currency}, un {pct}% menos que antes."),

    ("AR", "strong","💸 Esto es una locura: {origin} → {destination} bajó {pct}%",  "Hoy desde {price} {currency}. ¡Re copado!"),
    ("AR", "strong","Che, se armó la ganga 🔥 {origin} → {destination}",            "Bajó {pct}%. Desde {price} {currency}, posta."),
    ("AR", "strong","Gran bajada en {origin} → {destination} 🚀",                   "¡{pct}% menos! Salís desde {price} {currency}. No aflojes."),
    ("AR", "strong","Fiera, esto es un golazo 🎉",                                   "{origin} → {destination}: -{pct}%, desde {price} {currency}."),
    ("AR", "strong","💸 {pct}% de descuento: {origin} → {destination}",             "Hoy desde {price} {currency}. Una masa total."),
    ("AR", "strong","Re barato: {origin} → {destination} ✈️",                       "Cayó {pct}%. Salís desde {price} {currency}. ¡Arrancá!"),
    ("AR", "strong","Che, mirá este precio 👀",                                      "{origin} → {destination}: -{pct}%, hoy desde {price} {currency}. Capazo."),
    ("AR", "strong","🏆 El mejor precio en meses: {origin} → {destination}",        "Bajó {pct}%, desde {price} {currency}. Se armó."),
    ("AR", "strong","No te quedés sin verlo: {origin} → {destination} bajó {pct}% 💥","Desde {price} {currency}. Quilombo de oferta."),
    ("AR", "strong","Precio de capo: {origin} → {destination}",                     "-{pct}% y sale desde {price} {currency}. ¡Una joya!"),

    ("AR", "urgent","🔥 PRECIO MÍNIMO: {origin} → {destination} bajó {pct}%",       "¡Solo {price} {currency}! No dejes escapar esta."),
    ("AR", "urgent","⚡ Bolazo total: {origin} → {destination} -{pct}%",             "Desde {price} {currency}. Posta, es ahora o nunca."),
    ("AR", "urgent","Che, ¡esto es histórico! {origin} → {destination}",            "Bajó {pct}%, hoy desde {price} {currency}. ¡Una locura!"),
    ("AR", "urgent","🚨 Oportunidad única: {origin} → {destination}",               "-{pct}% y sale {price} {currency}. ¡Dale ya, fiera!"),
    ("AR", "urgent","Re baratísimo: {origin} → {destination} ✈️🔥",                 "¡{pct}% de bajada! Desde {price} {currency}. Ahora o nunca."),
    ("AR", "urgent","Precio de película: {origin} → {destination} 💥",              "Cayó {pct}%. Desde {price} {currency}. ¡Un caponazo!"),
    ("AR", "urgent","🏆 La oferta del año: {origin} → {destination}",               "-{pct}%, hoy desde {price} {currency}. ¡Una masa!"),
    ("AR", "urgent","¡No lo puedo creer! {origin} → {destination} bajó {pct}% 😱",  "Hoy desde {price} {currency}. ¡Arrancá ya, chabón!"),

    ("AR", "reengagement","¿Todavía pensás en volar a {destination}? 🛫",           "Viste {price} {currency} antes. ¡Dale che, es tu momento!"),
    ("AR", "reengagement","Che, ¿te vas a {destination}? ✈️",                       "Tenías {price} {currency} en el radar. No lo dejes escapar, fiera."),
    ("AR", "reengagement","Fiera, ¿qué pasó con el viaje a {destination}?",         "Viste {price} {currency} antes. ¡Ese precio es una joya!"),
    ("AR", "reengagement","{destination} te está esperando 🌎",                     "Viste precios desde {price} {currency}. ¿Arrancamos?"),
    ("AR", "reengagement","¿Se armó el viaje a {destination}? 🤔",                  "Encontraste {price} {currency}. Posta que vale la pena."),

    # =========================================================
    # MÉXICO  (MX)  — 23 templates
    # =========================================================
    ("MX", "soft",  "Órale, {origin} → {destination} bajó {pct}% ✈️",              "Hoy desde {price} {currency}. ¡Está chido!"),
    ("MX", "soft",  "¡No manches! {origin} → {destination} se puso bueno",         "Bajó {pct}%. Desde {price} {currency}, está de pelos."),
    ("MX", "soft",  "Güey, ¿viste? {origin} → {destination} bajó {pct}%",          "Sale desde {price} {currency}. ¡Qué onda tan chida!"),
    ("MX", "soft",  "✈️ {origin} → {destination} cayó {pct}%",                     "Desde {price} {currency}. Sale, no te rajes."),
    ("MX", "soft",  "Ahorita conviene: {origin} → {destination} ✈️",               "Bajó {pct}%. Sale desde {price} {currency}. ¡Dale gas!"),
    ("MX", "soft",  "Qué onda chida en {origin} → {destination} 🎉",               "-{pct}%, hoy desde {price} {currency}. Está a neta."),
    ("MX", "soft",  "De neta: {origin} → {destination} bajó {pct}% 🛫",            "Sale desde {price} {currency}. ¡Está heavy!"),

    ("MX", "strong","💸 ¡No manches qué precio! {origin} → {destination}",          "Bajó {pct}%. Desde {price} {currency}. ¡A huevo!"),
    ("MX", "strong","🔥 Gran bajada: {origin} → {destination} -{pct}%",             "Sale desde {price} {currency}. ¡Órale, es la neta!"),
    ("MX", "strong","Güey, esto está cañón 💥 {origin} → {destination}",            "-{pct}%, desde {price} {currency}. ¡Está de pelos!"),
    ("MX", "strong","¡Sale y vale! {origin} → {destination} bajó {pct}% ✈️",       "Desde {price} {currency}. Dale gas, cuate."),
    ("MX", "strong","🏆 Precio histórico: {origin} → {destination}",               "Bajó {pct}%, hoy desde {price} {currency}. ¡Qué pasada!"),
    ("MX", "strong","¡Aguas que se va! {origin} → {destination} -{pct}% 🚀",        "Desde {price} {currency}. ¡La neta es ahorita!"),
    ("MX", "strong","🎯 Oportunidad chida: {origin} → {destination}",              "-{pct}% y sale desde {price} {currency}. ¡Está bien macizo!"),

    ("MX", "urgent","🔥 ¡A huevo! {origin} → {destination} bajó {pct}%",            "¡Solo {price} {currency}! ¡Órale ya, güey!"),
    ("MX", "urgent","⚡ Precio mínimo: {origin} → {destination} -{pct}%",           "Desde {price} {currency}. ¡No manches, es ahorita!"),
    ("MX", "urgent","¡Esto está cañonísimo! {origin} → {destination} 💥",           "Bajó {pct}%, desde {price} {currency}. ¡Dale gas ya!"),
    ("MX", "urgent","🚨 ¡Aguas! La mejor oferta del año: {origin} → {destination}", "-{pct}%, sale {price} {currency}. ¡Sale cuate!"),
    ("MX", "urgent","¡De pelos y pa' arriba! {origin} → {destination} -{pct}% 🎊",  "Hoy desde {price} {currency}. ¡La neta!"),
    ("MX", "urgent","🔥 Oferta de impacto: {origin} → {destination} bajó {pct}%",  "¡Desde {price} {currency} nomás! ¡Órale, no te rajes!"),

    ("MX", "reengagement","¿Ya vas a {destination}, güey? 🛫",                     "Viste {price} {currency} antes. ¡Órale, no te rajes!"),
    ("MX", "reengagement","¿Qué onda con el viaje a {destination}? ✈️",            "Encontraste precios desde {price} {currency}. ¡Dale gas!"),
    ("MX", "reengagement","Ahorita o nunca: {destination} te espera 🌎",           "Tenías {price} {currency} en el radar. ¡Sale!"),

    # =========================================================
    # COLOMBIA  (CO)  — 15 templates
    # =========================================================
    ("CO", "soft",  "Parce, {origin} → {destination} bajó {pct}% ✈️",              "Hoy desde {price} {currency}. ¡Qué bacano!"),
    ("CO", "soft",  "¡Qué nota! {origin} → {destination} cayó {pct}%",             "Sale desde {price} {currency}. ¡Está chimba!"),
    ("CO", "soft",  "Broder, bajó el tiquete {origin} → {destination}",            "-{pct}%. Desde {price} {currency}. ¡Berraco!"),
    ("CO", "soft",  "Oportunidad bacana: {origin} → {destination} ✈️",             "Bajó {pct}%, hoy desde {price} {currency}. ¡Dale pues!"),
    ("CO", "soft",  "✈️ Pilas con esto: {origin} → {destination} -{pct}%",         "Sale desde {price} {currency}. ¡Está de chimba!"),

    ("CO", "strong","💸 ¡Qué chimba! {origin} → {destination} bajó {pct}%",         "Desde {price} {currency}. ¡Eso sí es bacano, parce!"),
    ("CO", "strong","Parce, esto está berraco 🔥 {origin} → {destination}",         "-{pct}%, desde {price} {currency}. ¡Qué vaina tan nota!"),
    ("CO", "strong","¡No hay tos! Gran bajada en {origin} → {destination}",         "Cayó {pct}%. Sale desde {price} {currency}. ¡De una, broder!"),
    ("CO", "strong","🏆 Precio histórico: {origin} → {destination} -{pct}%",        "Hoy desde {price} {currency}. ¡Esto sí es chimba, parce!"),

    ("CO", "urgent","🔥 ¡Bacano pues! {origin} → {destination} bajó {pct}%",        "¡Solo {price} {currency}! ¡De una, parce!"),
    ("CO", "urgent","⚡ ¡Eso sí es una oferta! {origin} → {destination} -{pct}%",   "Desde {price} {currency}. ¡Berraco, broder!"),
    ("CO", "urgent","🚨 Precio mínimo: {origin} → {destination}",                   "Bajó {pct}%, desde {price} {currency}. ¡Pilas que se acaba!"),
    ("CO", "urgent","¡Chimba de oferta! {origin} → {destination} -{pct}% 🎊",       "Hoy {price} {currency}. ¡De una parce, no la dejes ir!"),

    ("CO", "reengagement","¿Qué pasó con el viaje a {destination}, parce? ✈️",      "Viste precios desde {price} {currency}. ¡Dale pues!"),
    ("CO", "reengagement","{destination} te espera, broder 🌎",                     "Encontraste {price} {currency}. ¡Qué chimba, de una!"),

    # =========================================================
    # CHILE  (CL)  — 12 templates
    # =========================================================
    ("CL", "soft",  "Wena po, {origin} → {destination} bajó {pct}% ✈️",            "Hoy desde {price} {currency}. ¡Al tiro, cachai!"),
    ("CL", "soft",  "Filete: {origin} → {destination} cayó {pct}% 🛫",             "Sale desde {price} {currency}. ¡Bacán po!"),
    ("CL", "soft",  "✈️ Bajó caleta: {origin} → {destination} -{pct}%",            "Hoy desde {price} {currency}. ¡La raja, weón!"),

    ("CL", "strong","💸 ¡La raja! {origin} → {destination} bajó {pct}%",            "Desde {price} {currency}. ¡Al tiro po!"),
    ("CL", "strong","Gran bajada po: {origin} → {destination} -{pct}% 🔥",          "Hoy desde {price} {currency}. ¡Filete de oferta!"),
    ("CL", "strong","¡Qué onda más bacán! {origin} → {destination} -{pct}% ✈️",     "Sale desde {price} {currency}. ¡Wena po!"),
    ("CL", "strong","🏆 Precio histórico: {origin} → {destination}",               "Bajó {pct}% po. Desde {price} {currency}. ¡Cachai qué oferta!"),

    ("CL", "urgent","🔥 ¡La raja total! {origin} → {destination} bajó {pct}%",      "¡Solo {price} {currency} po! ¡Al tiro weón!"),
    ("CL", "urgent","⚡ Precio mínimo: {origin} → {destination} -{pct}%",           "Desde {price} {currency}. ¡No po, es ahora o nunca!"),
    ("CL", "urgent","🚨 ¡Filetísimo! {origin} → {destination} -{pct}% 💥",          "Hoy {price} {currency}. ¡Al tiro po, cachai!"),

    ("CL", "reengagement","¿Todavía vai a {destination}, weón? 🛫",                 "Viste {price} {currency} antes. ¡Al tiro po!"),
    ("CL", "reengagement","{destination} te está esperando po ✈️",                  "Encontraste {price} {currency}. ¡Wena, de una!"),

    # =========================================================
    # ESPAÑA  (ES)  — 11 templates
    # =========================================================
    ("ES", "soft",  "Tío, {origin} → {destination} bajó {pct}% ✈️",                "Hoy desde {price} {currency}. ¡Mola mazo!"),
    ("ES", "soft",  "¡Guay! {origin} → {destination} cayó {pct}% 🛫",              "Sale desde {price} {currency}. ¡Qué pasada!"),
    ("ES", "soft",  "✈️ {origin} → {destination}: -{pct}%, flipante",              "Hoy desde {price} {currency}. ¡Está chulo, tío!"),

    ("ES", "strong","💸 ¡Esto mola! {origin} → {destination} bajó {pct}%",          "Desde {price} {currency}. ¡Ostras tía, qué precio!"),
    ("ES", "strong","Gran bajada: {origin} → {destination} -{pct}% 🔥",             "Hoy desde {price} {currency}. ¡Cojonudo, tío!"),
    ("ES", "strong","¡Madre mía! {origin} → {destination} bajó {pct}% ✈️",         "Sale desde {price} {currency}. ¡Mola un montón!"),
    ("ES", "strong","🏆 Precio histórico: {origin} → {destination}",               "Bajó {pct}%. Desde {price} {currency}. ¡Tiene tela marinera!"),

    ("ES", "urgent","🔥 ¡Flipas! {origin} → {destination} bajó {pct}%",             "¡Solo {price} {currency}! ¡A por ello, tío!"),
    ("ES", "urgent","⚡ Precio mínimo: {origin} → {destination} -{pct}%",           "Desde {price} {currency}. ¡Cojonudo, no hay nada igual!"),

    ("ES", "reengagement","¿Sigues pensando en volar a {destination}, tío? 🛫",     "Viste {price} {currency} antes. ¡Mola mucho, no lo dejes!"),
    ("ES", "reengagement","{destination} te espera, tía ✈️",                        "Encontraste {price} {currency}. ¡Ostras, no lo dejes escapar!"),

    # =========================================================
    # GENÉRICO  (*)  — 8 templates (fallback para cualquier país)
    # =========================================================
    ("*",  "soft",  "{origin} → {destination} bajó {pct}% ✈️",                     "Hoy desde {price} {currency}. ¡Buen momento para volar!"),
    ("*",  "soft",  "Oferta: {origin} → {destination} -{pct}% 🛫",                  "Sale desde {price} {currency}. ¡No te lo pierdas!"),

    ("*",  "strong","💸 Gran bajada: {origin} → {destination} -{pct}%",              "Hoy desde {price} {currency}. ¡Excelente precio!"),
    ("*",  "strong","🔥 Precio histórico: {origin} → {destination}",                "Bajó {pct}%. Desde {price} {currency}. No te lo pierdas."),

    ("*",  "urgent","🚨 Precio mínimo: {origin} → {destination} -{pct}%",           "¡Solo {price} {currency}! Esta oferta no dura."),
    ("*",  "urgent","⚡ Oferta imperdible: {origin} → {destination} bajó {pct}% 💥","Desde {price} {currency}. ¡Es ahora o nunca!"),

    ("*",  "reengagement","¿Todavía pensás en volar a {destination}? 🛫",           "Viste precios desde {price} {currency}. ¡No lo dejes escapar!"),
    ("*",  "reengagement","{destination} te está esperando ✈️",                     "Encontraste {price} {currency}. ¡Es el momento de volar!"),
]


async def seed(clear: bool = False) -> None:
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        if clear:
            print("Marcando todos los templates existentes como inactivos...")
            await session.execute(
                update(NotificationTemplate).values(is_active=False)
            )
            await session.commit()

        inserted = 0
        for country_code, drop_level, title, body in TEMPLATES:
            session.add(NotificationTemplate(
                country_code=country_code,
                drop_level=drop_level,
                title_template=title,
                body_template=body,
                is_active=True,
            ))
            inserted += 1

        await session.commit()
        print(f"✅ {inserted} templates insertados.")

    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed notification_templates")
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Marcar todos los templates existentes como inactivos antes de insertar",
    )
    args = parser.parse_args()

    asyncio.run(seed(clear=args.clear))
