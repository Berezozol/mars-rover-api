"""Class table of `classify_total` (docs/proposals/overload-protection.md section 6, with the
decision of 2026-11-21: stops/near + sample_type -> scan_light; + camera/dates/meta ->
scan_heavy). Shared by the unit test (AC-1) and the integration test of `_total` (AC-2).

Each row is `(id, PhotoFilter kwargs, expected class)`; the class is a `TotalSource` member
name, or None when the spec allows either `summary` or `scan_light` ("as now": a sol or a day
pins the search) - such rows must only never be `scan_heavy`.
"""

from datetime import date
from typing import Any

from app.repositories.photos import PhotoFilter

ROVER = "curiosity"
NEAR = (10.0, 20.0, 5.0)
CAMERA = {"camera_codes": ["FHAZ"], "camera_ids": [1]}
SOLS = {"sol_min": 1, "sol_max": 100}
TIME_WINDOW = ((3600, 7200),)

# (id, kwargs, expected member name or None for "summary or scan_light")
CASES: list[tuple[str, dict[str, Any], str | None]] = [
    # --- summary: answered by sol_summaries / camera_sol_counts ---------------------------------
    ("nothing", {}, "SUMMARY"),
    ("sol-range", dict(SOLS), "SUMMARY"),
    ("sol-min-only", {"sol_min": 5}, "SUMMARY"),
    ("camera", dict(CAMERA), "SUMMARY"),
    ("camera-full", {**CAMERA, "sample_type": "full"}, "SUMMARY"),
    ("camera-sol-range", {**CAMERA, **SOLS}, "SUMMARY"),
    ("camera-full-sol-range", {**CAMERA, **SOLS, "sample_type": "full"}, "SUMMARY"),
    ("full", {"sample_type": "full"}, "SUMMARY"),
    ("full-sol-range", {"sample_type": "full", **SOLS}, "SUMMARY"),
    ("sol", {"sol": 1000}, "SUMMARY"),
    # --- pinned by sol or day: summary or scan_light, never scan_heavy --------------------------
    ("sol-camera", {"sol": 1000, **CAMERA}, None),
    ("sol-subframe", {"sol": 1000, "sample_type": "subframe"}, None),
    ("sol-min-width", {"sol": 1000, "min_width": 1}, None),
    ("sol-local-time", {"sol": 1000, "local_time_ranges": TIME_WINDOW}, None),
    ("sol-date-min", {"sol": 1000, "date_min": date(2020, 1, 1)}, None),
    ("sol-near", {"sol": 1000, "near": NEAR}, None),
    ("sol-site-camera", {"sol": 1000, "site": 5, **CAMERA}, None),
    ("earth-date", {"earth_date": date(2020, 1, 1)}, None),
    ("earth-date-camera", {"earth_date": date(2020, 1, 1), **CAMERA}, None),
    ("earth-date-subframe", {"earth_date": date(2020, 1, 1), "sample_type": "subframe"}, None),
    ("earth-date-min-height", {"earth_date": date(2020, 1, 1), "min_height": 1}, None),
    ("earth-date-near", {"earth_date": date(2020, 1, 1), "near": NEAR}, None),
    # --- scan_light: stops / near alone, or with full ------------------------------------------
    ("site", {"site": 5}, "SCAN_LIGHT"),
    ("site-drive-range", {"site": 5, "drive_from": 1, "drive_to": 9}, "SCAN_LIGHT"),
    ("site-min", {"site_min": 5}, "SCAN_LIGHT"),
    ("site-range", {"site_min": 0, "site_max": 100000}, "SCAN_LIGHT"),
    ("near", {"near": NEAR}, "SCAN_LIGHT"),
    ("near-sol-range", {"near": NEAR, **SOLS}, "SCAN_LIGHT"),
    ("site-range-full", {"site_min": 0, "site_max": 100000, "sample_type": "full"}, "SCAN_LIGHT"),
    ("site-full", {"site": 5, "sample_type": "full"}, "SCAN_LIGHT"),
    ("near-full", {"near": NEAR, "sample_type": "full"}, "SCAN_LIGHT"),
    # The decision says "+ sample_type" (the column is in the index), not "+ full only".
    ("site-subframe", {"site": 5, "sample_type": "subframe"}, "SCAN_LIGHT"),
    ("near-subframe", {"near": NEAR, "sample_type": "subframe"}, "SCAN_LIGHT"),
    # --- scan_heavy: not pinned and counted over the heap --------------------------------------
    ("date-min", {"date_min": date(2020, 1, 1)}, "SCAN_HEAVY"),
    ("date-max", {"date_max": date(2020, 1, 1)}, "SCAN_HEAVY"),
    ("date-range-full", {"date_min": date(2020, 1, 1), "sample_type": "full"}, "SCAN_HEAVY"),
    ("date-and-sol-range", {"date_min": date(2020, 1, 1), **SOLS}, "SCAN_HEAVY"),
    ("camera-date", {**CAMERA, "date_min": date(2020, 1, 1)}, "SCAN_HEAVY"),
    ("local-time", {"local_time_ranges": TIME_WINDOW}, "SCAN_HEAVY"),
    ("local-time-two-windows", {"local_time_ranges": ((0, 3600), (7200, 9000))}, "SCAN_HEAVY"),
    ("min-width", {"min_width": 1}, "SCAN_HEAVY"),
    ("min-height", {"min_height": 1}, "SCAN_HEAVY"),
    ("min-width-full", {"min_width": 1, "sample_type": "full"}, "SCAN_HEAVY"),
    ("subframe", {"sample_type": "subframe"}, "SCAN_HEAVY"),
    ("downsampled", {"sample_type": "downsampled"}, "SCAN_HEAVY"),
    ("subframe-sol-range", {"sample_type": "subframe", **SOLS}, "SCAN_HEAVY"),
    ("subframe-camera", {"sample_type": "subframe", **CAMERA}, "SCAN_HEAVY"),
    ("site-camera", {"site": 5, **CAMERA}, "SCAN_HEAVY"),
    ("site-range-camera", {"site_min": 0, "site_max": 100000, **CAMERA}, "SCAN_HEAVY"),
    ("site-camera-full", {"site": 5, **CAMERA, "sample_type": "full"}, "SCAN_HEAVY"),
    ("near-camera", {"near": NEAR, **CAMERA}, "SCAN_HEAVY"),
    ("site-date", {"site": 5, "date_min": date(2020, 1, 1)}, "SCAN_HEAVY"),
    ("near-date", {"near": NEAR, "date_max": date(2020, 1, 1)}, "SCAN_HEAVY"),
    ("site-min-width", {"site": 5, "min_width": 1}, "SCAN_HEAVY"),
    ("near-local-time", {"near": NEAR, "local_time_ranges": TIME_WINDOW}, "SCAN_HEAVY"),
]

CASE_IDS = [case[0] for case in CASES]


def make_filter(kwargs: dict[str, Any]) -> PhotoFilter:
    return PhotoFilter(rover_id=ROVER, **kwargs)
