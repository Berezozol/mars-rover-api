"""Read logic of ``GET /api/v2/rovers/{rover}/weather[/{sol}]`` (docs/API.md, stage 17).

The summaries are stored in K, Pa, %, W/m2 and m/s as the source gives them; this module converts
on read: temperatures to degrees Celsius (to 0.1), pressure to 0.1 Pa, humidity to 0.1 %, UV to
0.01 W/m2, wind to 0.1 m/s, coverage to 0.01. A parameter key is in the answer only when the
rover has that sensor in our data (Curiosity: no wind; Perseverance: no UV, and no ground
temperature when WEATHER_M20_GROUND_ENABLED is off).
"""

import asyncio
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import InvalidParam, NotFoundError, SolNotFound
from app.repositories import weather as weather_repo
from app.services.rover_lookup import find_rover
from app.services.weather_sync import FLAG_ORDER

WEATHER_ROVERS = ("curiosity", "perseverance")
KELVIN = 273.15
# The ground temperature of Perseverance: its answer key, flag parameter and source product.
GROUND_PARAMETER = "ground_temp_c"
GROUND_PRODUCT = "DER_TIRS"
CREDITS = {
    "perseverance": (
        "Rodriguez-Manfredi, J. A.; de la Torre Juarez, M. (MEDA, Mars 2020) / "
        "NASA PDS Atmospheres Node, urn:nasa:pds:mars2020_meda"
    ),
    "curiosity": (
        "REMS team (Centro de Astrobiologia) / NASA PDS Atmospheres Node, urn:nasa:pds:msl_rems"
    ),
}
# Where the files of the rover are listed (the link of a file is the base URL plus its path).
_SOURCE_SUFFIX = {"perseverance": "", "curiosity": "DATA/"}

Json = dict[str, Any]
JsonList = list[Json]


@dataclass(frozen=True)
class WeatherData:
    """The list answer: the header values and the summaries as ready-to-send dicts."""

    rover: str
    credit: str
    source_url: str
    parameters: Json
    updated_at: datetime | None
    source_max_sol: int | None
    items: list[Json]

    def between(self, sol_min: int | None, sol_max: int | None) -> "WeatherData":
        """The same answer with only the items in ``[sol_min, sol_max]`` (items are by sol)."""
        if sol_min is None and sol_max is None:
            return self
        low = 0 if sol_min is None else bisect_left(self.items, sol_min, key=_sol_of)
        high = (
            len(self.items) if sol_max is None else bisect_right(self.items, sol_max, key=_sol_of)
        )
        return replace(self, items=self.items[low:high])


@dataclass(frozen=True)
class WeatherDetail:
    """One sol: the summary, its 24 hours and the files it was computed from."""

    rover: str
    item: Json
    hours: list[Json]
    files: list[Json]


def _sol_of(item: Json) -> int:
    return int(item["sol"])


def _round(value: float, digits: int) -> float:
    """Round and never give ``-0.0``."""
    rounded = round(value, digits)
    return 0.0 if rounded == 0 else rounded


def _celsius(kelvin: float | None) -> float | None:
    return None if kelvin is None else _round(kelvin - KELVIN, 1)


def _value(value: float | None, digits: int) -> float | None:
    return None if value is None else _round(value, digits)


def _stat3(low: float | None, high: float | None, mean: float | None, convert: Any) -> Json | None:
    if low is None or high is None or mean is None:
        return None
    return {"min": convert(low), "max": convert(high), "mean": convert(mean)}


def _pressure(value: float | None) -> float | None:
    return _value(value, 1)


def _humidity(value: float | None) -> float | None:
    return _value(value, 1)


def _flags(flags: Sequence[Json], *, ground: bool = True) -> list[Json]:
    """The remarks in the order of the dictionary, inside a code by ``parameter``; without
    ``ground`` the remarks about the ground temperature are left out."""
    known = {code: index for index, code in enumerate(FLAG_ORDER)}
    ordered = sorted(
        (f for f in flags if ground or f.get("parameter") != GROUND_PARAMETER),
        key=lambda f: (known.get(f["code"], len(known)), f["code"], f.get("parameter") or ""),
    )
    return [
        {"code": f["code"], "parameter": f.get("parameter"), "count": f.get("count")}
        for f in ordered
    ]


class WeatherService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    # Perseverance's ground temperature is an estimate (17G, MEDA DER_TIRS); the flag is a switch.
    def _has_ground(self, rover: str) -> bool:
        return rover == "curiosity" or self._settings.weather_m20_ground_enabled

    def parameters(self, rover: str) -> Json:
        """The keys of the rover's parameters with their unit and quality."""
        found: Json = {
            "air_temp_c": {"unit": "°C", "quality": "measured"},
            "pressure_pa": {"unit": "Pa", "quality": "measured"},
            "humidity_pct": {"unit": "%", "quality": "preliminary"},
        }
        if rover == "perseverance":
            found["wind"] = {"unit": "m/s", "quality": "measured"}
        if self._has_ground(rover):
            quality = "measured" if rover == "curiosity" else "estimate"
            found["ground_temp_c"] = {"unit": "°C", "quality": quality}
        if rover == "curiosity":
            found["uv_wm2"] = {"unit": "W/m²", "quality": "measured"}
        return found

    def source_url(self, rover: str) -> str:
        base = (
            self._settings.weather_meda_base_url
            if rover == "perseverance"
            else self._settings.weather_rems_base_url
        )
        return base.rstrip("/") + "/" + _SOURCE_SUFFIX[rover]

    def _link_base(self, rover: str) -> str:
        base = (
            self._settings.weather_meda_base_url
            if rover == "perseverance"
            else self._settings.weather_rems_base_url
        )
        return base.rstrip("/") + "/"

    async def check(
        self, rover: str, sol_min: int | None = None, sol_max: int | None = None
    ) -> str:
        """The checks of ``list`` without the query (so a cached response can be served);
        returns the rover id."""
        found = await find_rover(self._session, rover)
        if found.id not in WEATHER_ROVERS:
            raise NotFoundError(f"Rover {found.id} has no weather data")
        if sol_min is not None and sol_max is not None and sol_min > sol_max:
            raise InvalidParam("sol_min", "sol_min must be less than or equal to sol_max")
        return found.id

    async def fingerprint(self, rover: str) -> tuple[Any, ...]:
        """Changes when the stored summaries or the 17G switch change; the key of cached
        responses."""
        stored = await weather_repo.daily_fingerprint(self._session, rover)
        return (*stored, self._settings.weather_m20_ground_enabled)

    async def list(
        self, rover: str, sol_min: int | None = None, sol_max: int | None = None
    ) -> WeatherData:
        """Summaries of a rover by sol, optionally within ``[sol_min, sol_max]``.

        A rover without weather -> ``NotFoundError``, an unknown one -> ``RoverNotFound``,
        ``sol_min`` > ``sol_max`` -> ``InvalidParam``. No data yet: no items and null dates.
        """
        rover = await self.check(rover, sol_min, sol_max)
        rows = await weather_repo.list_daily(self._session, rover, sol_min, sol_max)
        max_sol, changed = await weather_repo.daily_extent(self._session, rover)
        dataset = await weather_repo.get_dataset(self._session, rover)
        updated_at = (dataset.updated_at if dataset else None) or changed
        return WeatherData(
            rover,
            CREDITS[rover],
            self.source_url(rover),
            self.parameters(rover),
            updated_at if max_sol is not None else None,
            max_sol,
            await asyncio.to_thread(self._items, rover, rows),
        )

    def _items(self, rover: str, rows: Sequence[Row[Any]]) -> JsonList:
        return [self.item(rover, row) for row in rows]

    async def get_sol(self, rover: str, sol: int) -> WeatherDetail:
        """One sol with its hours and files; a sol without data -> ``SolNotFound``."""
        rover = await self.check(rover)
        row = await weather_repo.get_daily(self._session, rover, sol)
        if row is None:
            raise SolNotFound()
        hours = await weather_repo.list_hourly(self._session, rover, sol)
        files = await weather_repo.list_sol_files(self._session, rover, sol)
        base = self._link_base(rover)
        return WeatherDetail(
            rover,
            self.item(rover, row),
            self.hours(rover, hours),
            [
                {
                    "product": f.product,
                    "version": f.version,
                    "file": f.path,
                    "link": base + f.path,
                }
                for f in files
                if self._has_ground(rover) or f.product != GROUND_PRODUCT
            ],
        )

    def item(self, rover: str, row: Row[Any]) -> Json:
        """One ``WeatherSol`` as a dict (the key order is the JSON order)."""
        item: Json = {
            "sol": row.sol,
            "earth_date": row.earth_date,
            "ls": _value(row.ls, 1),
            "coverage": _value(row.coverage, 2),
            "hours_covered": row.hours_covered,
            "air_temp_c": _stat3(row.air_min_k, row.air_max_k, row.air_mean_k, _celsius),
            "pressure_pa": _stat3(
                row.pressure_min_pa, row.pressure_max_pa, row.pressure_mean_pa, _pressure
            ),
            "humidity_pct": _stat3(
                row.humidity_min_pct, row.humidity_max_pct, row.humidity_mean_pct, _humidity
            ),
        }
        if rover == "perseverance":
            item["wind"] = self._wind(row)
        if self._has_ground(rover):
            item["ground_temp_c"] = _stat3(
                row.ground_min_k, row.ground_max_k, row.ground_mean_k, _celsius
            )
        if rover == "curiosity":
            item["uv_wm2"] = self._uv(row)
        ground = self._has_ground(rover)
        item["flags"] = _flags(row.flags, ground=ground)
        item["source_version"] = dict(
            sorted((p, v) for p, v in row.source_version.items() if ground or p != GROUND_PRODUCT)
        )
        return item

    @staticmethod
    def _wind(row: Row[Any]) -> Json | None:
        if row.wind_mean_ms is None:
            return None
        return {
            "speed_mean_ms": _value(row.wind_mean_ms, 1),
            "gust_ms": _value(row.wind_gust_ms, 1),
            "direction_mode_deg": _value(row.wind_dir_mode_deg, 1),
        }

    @staticmethod
    def _uv(row: Row[Any]) -> Json | None:
        if row.uv_max_wm2 is None or row.uv_mean_wm2 is None:
            return None
        return {"max": _value(row.uv_max_wm2, 2), "mean": _value(row.uv_mean_wm2, 2)}

    def hours(self, rover: str, rows: Sequence[Row[Any]]) -> JsonList:
        """The 24 hours of a sol; an hour that is missing from the table is an empty one."""
        by_hour = {row.hour: row for row in rows}
        return [self._hour(rover, hour, by_hour.get(hour)) for hour in range(24)]

    def _hour(self, rover: str, hour: int, row: Row[Any] | None) -> Json:
        def pick(name: str, convert: Callable[[float | None], float | None]) -> float | None:
            return convert(getattr(row, name)) if row is not None else None

        def triple(
            key: str,
            mean: str,
            low: str,
            high: str,
            convert: Callable[[float | None], float | None],
        ) -> Json:
            return {
                key: pick(mean, convert),
                f"{key}_min": pick(low, convert),
                f"{key}_max": pick(high, convert),
            }

        out: Json = {
            "hour": hour,
            "coverage": _value(row.coverage, 2) if row is not None else 0.0,
            **triple("air_temp_c", "air_k", "air_min_k", "air_max_k", _celsius),
            **triple("pressure_pa", "pressure_pa", "pressure_min_pa", "pressure_max_pa", _pressure),
            **triple(
                "humidity_pct", "humidity_pct", "humidity_min_pct", "humidity_max_pct", _humidity
            ),
        }
        if rover == "perseverance":
            out["wind_speed_ms"] = pick("wind_ms", lambda v: _value(v, 1))
            out["wind_gust_ms"] = pick("wind_gust_ms", lambda v: _value(v, 1))
            out["wind_direction_deg"] = pick("wind_dir_deg", lambda v: _value(v, 1))
        if self._has_ground(rover):
            out.update(
                triple("ground_temp_c", "ground_k", "ground_min_k", "ground_max_k", _celsius)
            )
        if rover == "curiosity":
            out.update(
                triple("uv_wm2", "uv_wm2", "uv_min_wm2", "uv_max_wm2", lambda v: _value(v, 2))
            )
        return out
