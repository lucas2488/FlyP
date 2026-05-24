"""Templates para BR (portugués), UY, PY, PE, VE

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-24

Agrega 63 templates nuevos:
  BR (25) — Portuguese
  UY (10), PY (8), PE (10), VE (10)
Sin cambios de esquema.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TEMPLATES = [

    # =========================================================
    # BRASIL  (BR)  — 25 templates en PORTUGUÉS
    # =========================================================

    # --- soft (7) ---
    ("BR", "soft",  "Cara, {origin} → {destination} baixou {pct}% ✈️",       "Hoje de {price} {currency}. Que legal, aproveita!"),
    ("BR", "soft",  "Mano, tá barato voar para {destination}!",                "Caiu {pct}%. Só {price} {currency}. Partiu?!"),
    ("BR", "soft",  "✈️ Voo {origin} → {destination} com desconto!",           "Baixou {pct}%. De {price} {currency}. Não perde!"),
    ("BR", "soft",  "Topzera: {origin} → {destination} baixou {pct}% 🛫",      "A partir de {price} {currency}. Bora logo!"),
    ("BR", "soft",  "Véi, olha o preço: {origin} → {destination} -{pct}%",     "De {price} {currency}. Aproveita essa oferta!"),
    ("BR", "soft",  "Que bacana! {origin} → {destination} baixou {pct}% ✈️",   "De {price} {currency}. Manda ver, vai lá!"),
    ("BR", "soft",  "🎯 Desconto de {pct}%: {origin} → {destination}",         "A partir de {price} {currency}. Show de bola!"),

    # --- strong (8) ---
    ("BR", "strong","💸 Caramba! {origin} → {destination} baixou {pct}%",       "De {price} {currency}. Isso sim é oferta, mano!"),
    ("BR", "strong","🔥 Promoção incrível: {origin} → {destination} -{pct}%",   "De {price} {currency}. Bora que é top demais!"),
    ("BR", "strong","Véi, que oferta! {origin} → {destination} -{pct}% ✈️",     "Só {price} {currency}. Não deixa passar não!"),
    ("BR", "strong","💥 {pct}% a menos: {origin} → {destination}",              "De {price} {currency}. Tá demais, cara!"),
    ("BR", "strong","Irmão, olha esse preço 👀 {origin} → {destination}",       "Baixou {pct}%. De {price} {currency}. Que massa!"),
    ("BR", "strong","🏆 Melhor preço do mês: {origin} → {destination}",         "Caiu {pct}%. De {price} {currency}. Topzera!"),
    ("BR", "strong","Rapaz, tá voando: {origin} → {destination} -{pct}% 🚀",    "De {price} {currency}. Aproveita logo!"),
    ("BR", "strong","Show de bola! {origin} → {destination} baixou {pct}% 🎉",  "Só {price} {currency}. Manda ver, irmão!"),

    # --- urgent (6) ---
    ("BR", "urgent","🔥 PREÇO MÍNIMO: {origin} → {destination} -{pct}%",        "Só {price} {currency}! Corre que é agora!"),
    ("BR", "urgent","⚡ Tá de graça, véi! {origin} → {destination} -{pct}%",    "De {price} {currency}. Não perde essa, mano!"),
    ("BR", "urgent","🚨 Oferta histórica: {origin} → {destination}",            "Baixou {pct}%! De {price} {currency}. Bora!"),
    ("BR", "urgent","Caramba, isso é demais! {origin} → {destination} -{pct}% 💥","Só {price} {currency}! É agora ou nunca, cara!"),
    ("BR", "urgent","⚡ O melhor desconto do ano: {origin} → {destination} 🎊", "-{pct}%! De {price} {currency}. Parte já, irmão!"),
    ("BR", "urgent","🔥 Isso tá topzera! {origin} → {destination} -{pct}%",     "Só {price} {currency}! Não deixa passar!"),

    # --- reengagement (4) ---
    ("BR", "reengagement","Ainda pensando em voar para {destination}? 🛫",       "Você viu {price} {currency}. Bora, mano!"),
    ("BR", "reengagement","Ei, e a viagem para {destination}? ✈️",               "Tinha {price} {currency}. Aproveita enquanto dá!"),
    ("BR", "reengagement","{destination} tá te chamando 🌎",                     "Você viu preços de {price} {currency}. Partiu!"),
    ("BR", "reengagement","Cara, vai voar para {destination}? 🤔",               "Encontrou {price} {currency}. Manda ver!"),

    # =========================================================
    # URUGUAY  (UY)  — 10 templates
    # =========================================================

    # --- soft (3) ---
    ("UY", "soft",  "Che, {origin} → {destination} bajó {pct}% ✈️",             "Hoy desde {price} {currency}. ¡Ta bueno!"),
    ("UY", "soft",  "Mirá qué oferta: {origin} → {destination} -{pct}%",         "Desde {price} {currency}. ¡De una, ta!"),
    ("UY", "soft",  "✈️ {origin} → {destination} bajó {pct}%, botija",           "Sale desde {price} {currency}. ¡Ta piola!"),

    # --- strong (3) ---
    ("UY", "strong","💸 ¡Gran bajada! {origin} → {destination} -{pct}%",          "Desde {price} {currency}. ¡Ta de taquito!"),
    ("UY", "strong","Che, ¡esto ta copado! {origin} → {destination} bajó {pct}%", "Desde {price} {currency}. ¡Dale ya!"),
    ("UY", "strong","🏆 Precio histórico: {origin} → {destination} -{pct}%",      "Desde {price} {currency}. ¡Ta buenísimo!"),

    # --- urgent (2) ---
    ("UY", "urgent","🔥 ¡Ta baratísimo! {origin} → {destination} bajó {pct}%",    "¡Solo {price} {currency}! ¡Ahora che!"),
    ("UY", "urgent","⚡ ¡De taquito! {origin} → {destination} -{pct}% 💥",        "Desde {price} {currency}. ¡Es ya o nunca, botija!"),

    # --- reengagement (2) ---
    ("UY", "reengagement","¿Todavía pensás en {destination}, botija? 🛫",          "Viste {price} {currency}. ¡Dale, ta!"),
    ("UY", "reengagement","¡{destination} te está esperando, che! ✈️",             "Viste precios desde {price} {currency}. ¡De una!"),

    # =========================================================
    # PARAGUAY  (PY)  — 8 templates
    # =========================================================

    # --- soft (2) ---
    ("PY", "soft",  "Che, {origin} → {destination} bajó {pct}% ✈️",              "Hoy desde {price} {currency}. ¡Al palo, compañero!"),
    ("PY", "soft",  "✈️ Oferta: {origin} → {destination} -{pct}%",               "Desde {price} {currency}. ¡Ta bien!"),

    # --- strong (2) ---
    ("PY", "strong","💸 Gran oferta: {origin} → {destination} bajó {pct}%",       "Desde {price} {currency}. ¡No te lo pierdas!"),
    ("PY", "strong","🔥 {origin} → {destination} bajó {pct}%, compañero!",        "Desde {price} {currency}. ¡Dale ya!"),

    # --- urgent (2) ---
    ("PY", "urgent","🚨 ¡Precio mínimo! {origin} → {destination} -{pct}%",        "¡Solo {price} {currency}! ¡Ahora, compañero!"),
    ("PY", "urgent","⚡ ¡Al palo! {origin} → {destination} -{pct}% 💥",           "Desde {price} {currency}. ¡Es ahora o nunca!"),

    # --- reengagement (2) ---
    ("PY", "reengagement","¿Todavía pensás en {destination}? 🛫",                 "Viste {price} {currency}. ¡Dale, compañero!"),
    ("PY", "reengagement","{destination} te espera ✈️",                           "Viste {price} {currency}. ¡No dejes pasar!"),

    # =========================================================
    # PERÚ  (PE)  — 10 templates
    # =========================================================

    # --- soft (3) ---
    ("PE", "soft",  "Causa, {origin} → {destination} bajó {pct}% ✈️",            "Desde {price} {currency}. ¡Qué bacán, pata!"),
    ("PE", "soft",  "Al toque: {origin} → {destination} -{pct}% 🛫",             "Desde {price} {currency}. ¡Habla pues!"),
    ("PE", "soft",  "✈️ Chévere: {origin} → {destination} bajó {pct}%",          "Sale desde {price} {currency}. ¡Qué nota, pe!"),

    # --- strong (3) ---
    ("PE", "strong","💸 ¡Qué bacanería! {origin} → {destination} bajó {pct}%",    "Desde {price} {currency}. ¡Al toque, causa!"),
    ("PE", "strong","🔥 Gran bajada: {origin} → {destination} -{pct}%",           "Desde {price} {currency}. ¡Habla pe, chévere!"),
    ("PE", "strong","Pata, mirá este precio 👀 {origin} → {destination}",         "-{pct}%, desde {price} {currency}. ¡De una!"),

    # --- urgent (2) ---
    ("PE", "urgent","🔥 ¡Precio mínimo! {origin} → {destination} -{pct}%",        "¡Solo {price} {currency}! ¡Al toque, causa!"),
    ("PE", "urgent","⚡ ¡Chévere total! {origin} → {destination} -{pct}% 💥",     "Desde {price} {currency}. ¡Es ahora pe!"),

    # --- reengagement (2) ---
    ("PE", "reengagement","¿Todavía pensás en volar a {destination}, causa? 🛫",   "Viste {price} {currency}. ¡Al toque pe!"),
    ("PE", "reengagement","{destination} te espera, pata ✈️",                      "Encontraste {price} {currency}. ¡Habla!"),

    # =========================================================
    # VENEZUELA  (VE)  — 10 templates
    # =========================================================

    # --- soft (3) ---
    ("VE", "soft",  "Chamo, {origin} → {destination} bajó {pct}% ✈️",            "Desde {price} {currency}. ¡Chévere, pana!"),
    ("VE", "soft",  "Pana, ¡mira esto! {origin} → {destination} -{pct}%",         "Sale desde {price} {currency}. ¡Burda de chévere!"),
    ("VE", "soft",  "✈️ {origin} → {destination} bajó {pct}%, no joda!",          "Desde {price} {currency}. ¡Qué vaina más bacán!"),

    # --- strong (3) ---
    ("VE", "strong","💸 ¡Chévere total! {origin} → {destination} bajó {pct}%",    "Desde {price} {currency}. ¡Dale, chamo!"),
    ("VE", "strong","🔥 Gran bajada: {origin} → {destination} -{pct}%",           "Desde {price} {currency}. ¡No joda, es verdad!"),
    ("VE", "strong","Pana, ¡esto está burda de barato! ✈️",                       "{origin} → {destination}: -{pct}%, desde {price} {currency}."),

    # --- urgent (2) ---
    ("VE", "urgent","🔥 ¡No joda, qué precio! {origin} → {destination} -{pct}%",  "¡Solo {price} {currency}! ¡Dale ya, chamo!"),
    ("VE", "urgent","⚡ ¡Burda de oferta! {origin} → {destination} -{pct}% 💥",   "Desde {price} {currency}. ¡Es ahora, pana!"),

    # --- reengagement (2) ---
    ("VE", "reengagement","¿Todavía pensás en {destination}, chamo? 🛫",           "Viste {price} {currency}. ¡Dale pana!"),
    ("VE", "reengagement","{destination} te está esperando, pana ✈️",              "Viste {price} {currency}. ¡No joda, es el momento!"),
]
# Total: BR(25) + UY(10) + PY(8) + PE(10) + VE(10) = 63 templates nuevos


def upgrade() -> None:
    for country_code, drop_level, title, body in _TEMPLATES:
        op.execute(
            sa.text(
                "INSERT INTO notification_templates "
                "(country_code, drop_level, title_template, body_template) "
                "VALUES (:c, :l, :t, :b)"
            ).bindparams(c=country_code, l=drop_level, t=title, b=body)
        )


def downgrade() -> None:
    for code in ("BR", "UY", "PY", "PE", "VE"):
        op.execute(
            sa.text(
                "DELETE FROM notification_templates WHERE country_code = :c"
            ).bindparams(c=code)
        )
