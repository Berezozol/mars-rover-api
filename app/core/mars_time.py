"""Martian time of a UTC moment: the Mars24 algorithm (Allison & McEwen 2000).

``solar_longitude`` (Ls), ``mars_sol_date`` (MSD) and ``local_mean_time`` (LMST): pure functions
without dependencies. Against the ``start_solar_longitude`` of 71 MEDA labels the Ls differs by
at most 0.004 degrees (stage 17); the model is good to about 0.01.
"""

import math
from datetime import UTC, date, datetime

_UNIX_EPOCH_JD = 2440587.5
_J2000_JD = 2451545.0
# Mars Sol Date: the epoch (JD 2451549.5 TT is MSD 44796.0 less a small offset) and the sol.
_MSD_EPOCH_JD = 2451549.5
_MSD_AT_EPOCH = 44796.0
_MSD_OFFSET = 0.0009626
_SOL_IN_DAYS = 1.0274912517
# An hour of LMST in SI seconds (a sol of 88 775.244 s over 24).
HOUR_LIVE_S = 3698.97
# TT = UTC + 32.184 s + the leap seconds (TAI - UTC) in force from the given date.
_TT_MINUS_TAI = 32.184
_TAI_MINUS_UTC = (
    (date(2017, 1, 1), 37),
    (date(2015, 7, 1), 36),
    (date(2012, 7, 1), 35),
    (date(2009, 1, 1), 34),
    (date(2006, 1, 1), 33),
)
# Planetary perturbers (amplitude in degrees, period in years, phase in degrees).
_PERTURBERS = (
    (0.0071, 2.2353, 49.409),
    (0.0057, 2.7543, 168.173),
    (0.0039, 1.1177, 191.837),
    (0.0037, 15.7866, 21.736),
    (0.0021, 2.1354, 15.704),
    (0.0020, 2.4694, 95.528),
    (0.0018, 32.8493, 49.095),
)


def _leap_seconds(day: date) -> int:
    return next((seconds for start, seconds in _TAI_MINUS_UTC if day >= start), 32)


def _as_utc(moment: datetime) -> datetime:
    return moment.astimezone(UTC) if moment.tzinfo else moment.replace(tzinfo=UTC)


def _julian_date_tt(moment: datetime) -> float:
    """Terrestrial Time as a Julian date; ``moment`` is aware (naive means UTC)."""
    utc = _as_utc(moment)
    jd_utc = _UNIX_EPOCH_JD + utc.timestamp() / 86400.0
    return jd_utc + (_TT_MINUS_TAI + _leap_seconds(utc.date())) / 86400.0


def mars_sol_date(moment: datetime) -> float:
    """Mars Sol Date (Mars24): days of 88 775.244 s from the Mars epoch, at ``moment``."""
    return (_julian_date_tt(moment) - _MSD_EPOCH_JD) / _SOL_IN_DAYS + _MSD_AT_EPOCH - _MSD_OFFSET


def local_mean_time(moment: datetime, lon_east_deg: float) -> tuple[int, float]:
    """The local Martian day number (``floor(MSD + lon / 360)``) and the local mean solar time
    in LMST seconds (0 <= s < 86 400, a sol has 86 400 LMST seconds) at east longitude
    ``lon_east_deg``."""
    local = mars_sol_date(moment) + lon_east_deg / 360.0
    day = math.floor(local)
    return day, min((local - day) * 86400.0, math.nextafter(86400.0, 0.0))


def solar_longitude(moment: datetime) -> float:
    """Ls in degrees, 0 <= Ls < 360, at ``moment`` (an aware datetime; naive means UTC)."""
    days = _julian_date_tt(moment) - _J2000_JD
    anomaly = math.radians(19.3871 + 0.52402073 * days)
    fictitious_sun = 270.3863 + 0.52403840 * days
    perturbation = sum(
        amplitude * math.cos(math.radians(0.985626 * days / period + phase))
        for amplitude, period, phase in _PERTURBERS
    )
    center = (
        (10.691 + 3.0e-7 * days) * math.sin(anomaly)
        + 0.623 * math.sin(2 * anomaly)
        + 0.050 * math.sin(3 * anomaly)
        + 0.005 * math.sin(4 * anomaly)
        + 0.0005 * math.sin(5 * anomaly)
        + perturbation
    )
    return (fictitious_sun + center) % 360.0
