"""Martian solar longitude (Ls) of a UTC moment: the Mars24 algorithm (Allison & McEwen 2000).

A pure function without dependencies. Against the ``start_solar_longitude`` of 71 MEDA labels
the result differs by at most 0.004 degrees (stage 17); the model is good to about 0.01.
"""

import math
from datetime import UTC, date, datetime

_UNIX_EPOCH_JD = 2440587.5
_J2000_JD = 2451545.0
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


def solar_longitude(moment: datetime) -> float:
    """Ls in degrees, 0 <= Ls < 360, at ``moment`` (an aware datetime; naive means UTC)."""
    utc = moment.astimezone(UTC) if moment.tzinfo else moment.replace(tzinfo=UTC)
    jd_utc = _UNIX_EPOCH_JD + utc.timestamp() / 86400.0
    jd_tt = jd_utc + (_TT_MINUS_TAI + _leap_seconds(utc.date())) / 86400.0
    days = jd_tt - _J2000_JD
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
