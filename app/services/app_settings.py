"""
Ajustes editables desde el dashboard (tabla app_settings), sin redeploy.

Cada ajuste tiene un default y un validador; lo que no está en la base usa el default.
Se cachean 30 s: un cambio desde el dashboard tarda como mucho eso en aplicarse.
"""
import time
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import AsyncSessionLocal
from app.models import AppSetting

CACHE_SECONDS = 30


@dataclass(frozen=True)
class Spec:
    default: Any
    label: str
    check: Callable[[Any], bool]
    hint: str


def _int(lo: int, hi: int) -> Callable[[Any], bool]:
    return lambda v: isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def _bool(v: Any) -> bool:
    return isinstance(v, bool)


SPECS: dict[str, Spec] = {
    "crowd_checker_enabled": Spec(True, "Búsquedas desde los celulares de usuarios", _bool,
                                  "Interruptor general: apagado, /checker/claim no reparte búsquedas a la app."),
    "claim_minutes": Spec(10, "Duración de la reserva (minutos)", _int(2, 60),
                          "Cuánto tiempo queda bloqueada una búsqueda para quien la reservó."),
    "poll_interval_seconds": Spec(45, "Pausa entre búsquedas (segundos)", _int(10, 3600),
                                  "Lo que la app espera entre una búsqueda completa y la siguiente."),
    "max_per_session": Spec(10, "Máximo por sesión de la app", _int(0, 500),
                            "Búsquedas que hace un aparato por apertura de la app."),
    "max_per_day": Spec(30, "Máximo por aparato por día", _int(0, 5000),
                        "Búsquedas que hace un aparato por día (hora Argentina)."),
    "double_check_enabled": Spec(False, "Doble verificación de bajadas raras", _bool,
                                 "Prendido: una bajada mayor al umbral no avisa hasta que otro aparato la confirme."),
    "double_check_threshold_pct": Spec(30, "Umbral de bajada rara (%)", _int(5, 95),
                                       "Bajada respecto del último precio conocido a partir de la cual es 'rara'."),
}

_cache: dict[str, Any] = {"at": 0.0, "values": None}


async def get_all(force: bool = False) -> dict[str, Any]:
    if not force and _cache["values"] is not None and time.monotonic() - _cache["at"] < CACHE_SECONDS:
        return _cache["values"]
    async with AsyncSessionLocal() as db:
        stored = {r.key: r.value for r in (await db.execute(select(AppSetting))).scalars()}
    values = {k: (stored[k] if k in stored and spec.check(stored[k]) else spec.default) for k, spec in SPECS.items()}
    _cache.update(at=time.monotonic(), values=values)
    return values


async def get(key: str) -> Any:
    return (await get_all())[key]


def describe(values: dict[str, Any]) -> list[dict]:
    return [{"key": k, "value": values[k], "default": s.default, "label": s.label, "hint": s.hint}
            for k, s in SPECS.items()]


class InvalidSetting(ValueError):
    pass


async def update(changes: dict[str, Any]) -> dict[str, Any]:
    for k, v in changes.items():
        spec = SPECS.get(k)
        if spec is None:
            raise InvalidSetting(f"ajuste desconocido: {k}")
        if not spec.check(v):
            raise InvalidSetting(f"valor inválido para {k}: {v!r}")
    async with AsyncSessionLocal() as db:
        for k, v in changes.items():
            stmt = pg_insert(AppSetting).values(key=k, value=v)
            await db.execute(stmt.on_conflict_do_update(index_elements=["key"], set_={"value": v}))
        await db.commit()
    return await get_all(force=True)
