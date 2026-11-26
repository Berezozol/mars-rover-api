"""v2 response models (docs/API.md, "Объекты"); field order is the JSON key order."""

from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    StrictInt,
    StrictStr,
    field_validator,
)
from pydantic.json_schema import SkipJsonSchema

from app.api.v2.params import MAX_SOL
from app.db.models import ApiKey, IngenuityFlight, Photo, RoverSample, SolSummary, SyncRun
from app.services import v2
from app.services.admin import RunPage
from app.services.api_keys import (
    MAX_TEXT_LENGTH,
    AccountDetail,
    AccountPage,
    AccountSummary,
    IssuedKey,
    normalize_email,
)
from app.services.locations import LocationData, PhotoLocation, TraverseData
from app.services.radiation import (
    RAD_CITATION,
    RAD_CREDIT,
    SOL_SECONDS,
    RadiationData,
    RadiationSolData,
)
from app.services.samples import SampleLocation, SamplesData, SampleView
from app.services.v2 import (
    CameraInfo,
    LatestPhotos,
    LocalTime,
    Manifest,
    NearestSols,
    NearStop,
    PhotoDetail,
    PhotoMeta,
    PhotoPage,
    RoverInfo,
    SolPosition,
)
from app.services.weather import WeatherData, WeatherDetail
from app.sources.photo_meta import MastAngles


def _utc_z(value: datetime) -> str:
    """``YYYY-MM-DDTHH:MM:SSZ`` in UTC, without fractional seconds."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


UtcDateTime = Annotated[datetime, PlainSerializer(_utc_z, return_type=str)]


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[dict[str, str]] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class RoverOut(BaseModel):
    id: str
    name: str
    mission: str
    launch_date: date
    landing_date: date
    end_date: date | None
    status: str
    max_sol: int | None
    max_date: date | None
    total_photos: int
    cameras_count: int
    last_synced_at: UtcDateTime | None
    data_source: Literal["sync", "archive"]


class RoversResponse(BaseModel):
    items: list[RoverOut]


class CameraOut(BaseModel):
    code: str
    name: str
    total_photos: int


class CamerasResponse(BaseModel):
    items: list[CameraOut]


class PhotoCameraOut(BaseModel):
    code: str
    name: str


class ImagesOut(BaseModel):
    small: str | None
    medium: str | None
    large: str | None
    full: str


class SourceOut(BaseModel):
    id: str
    link: str | None


class PhotoBase(BaseModel):
    id: int
    rover: str
    sol: int
    earth_date: date
    taken_at: UtcDateTime
    camera: PhotoCameraOut
    sample_type: str
    title: str | None
    img_src: str
    images: ImagesOut
    credit: str | None
    source: SourceOut
    width: int | None
    height: int | None


class LocalTimeOut(BaseModel):
    time: str
    kind: Literal["lmst", "ltst"]
    planet: Literal["mars"]


class MastOut(BaseModel):
    azimuth: float
    elevation: float


class NearStopOut(BaseModel):
    sol: int
    site: int
    drive: int
    lat: float
    lon: float
    distance_km: float


class PhotoOut(PhotoBase):
    local_time: LocalTimeOut | None
    mast: MastOut | None
    # Only in a search with ``near``; the key is absent otherwise.
    near_stop: NearStopOut | SkipJsonSchema[None] = None


class PhotoLocationOut(BaseModel):
    sol: int
    site: int
    drive: int
    lat: float
    lon: float
    elev_geoid: float | None
    # True: the photo was taken at this waypoint; False: later (the last waypoint before it).
    exact: bool


class InSolOut(BaseModel):
    position: int | None
    total: int
    newer_id: int | None
    older_id: int | None


class PhotoDetailOut(PhotoBase):
    caption: str | None
    received_at: UtcDateTime | None
    site: int | None
    drive: int | None
    extra: dict[str, Any]
    location: PhotoLocationOut | None
    local_time: LocalTimeOut | None
    mast: MastOut | None
    in_sol: InSolOut
    flight: int | None
    samples: list[int]


class FlightOut(BaseModel):
    """An Ingenuity flight; ``from``/``to`` are Python keywords, hence the aliases."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    flight: int
    sol: int
    date: date
    distance_m: int
    max_altitude_m: int
    max_groundspeed_mps: float
    duration_s: float
    from_: str = Field(alias="from")
    to: str | None
    total_photos: int


class FlightsResponse(BaseModel):
    items: list[FlightOut]


class SampleLocationOut(BaseModel):
    sol: int
    site: int
    drive: int
    lat: float
    lon: float
    elev_geoid: float | None
    match: Literal["exact", "nearest_before", "by_sol"]


class SampleOut(BaseModel):
    number: int
    name: str
    feature_name: str | None
    sampling_location: str | None
    status: Literal["on_rover", "depot"] | None
    current_location: str | None
    sample_type: Literal["rock_core", "regolith", "atmospheric"] | None
    sample_type_label: str | None
    rock_type: str | None
    sealed_on: date | None
    sol: int | None
    height_cm: float | None
    height_note: str | None
    dossier_url: str | None
    total_photos: int | None
    location: SampleLocationOut | None


class SamplesResponse(BaseModel):
    rover: str
    source_url: str
    source_updated_at: UtcDateTime | None
    updated_at: UtcDateTime | None
    items: list[SampleOut]


class DoseStats(BaseModel):
    mean: float
    min: float
    max: float


class ObservationCounts(BaseModel):
    used: int
    dropped_qc: int
    dropped_low_rate: int
    dropped_invalid: int


class RadiationSource(BaseModel):
    file: str
    link: str


class RadiationSol(BaseModel):
    sol: int
    earth_date: date
    dose_b_ugy_day: DoseStats
    dose_e_ugy_day: DoseStats
    count_rate_b_per_s: float
    coverage: float
    observations: ObservationCounts
    timing_caveat: bool | None
    source: RadiationSource


class RadiationHour(BaseModel):
    """One hour of LMST of a sol; doses in uGy/h (the daily fields are in uGy/day)."""

    hour: int = Field(description="Hour of local mean solar time, 0-23.")
    coverage: float = Field(description="Share of the hour covered by live time, 0-1, to 0.01.")
    observations: int
    dose_e_ugy_h: float | None = Field(description="Detector E, uGy/h to 0.01; null without data.")
    dose_b_ugy_h: float | None = Field(description="Detector B, uGy/h to 0.01; null without data.")
    count_rate_b_per_s: float | None = Field(
        description="Count rate of detector B per second of live time, to 0.001."
    )


class RadiationSolDetail(RadiationSol):
    rover: str
    credit: str
    citation: str
    hours: list[RadiationHour] | None = Field(
        description=(
            "24 hours of LMST; null when the hours of the sol are not calculated or its "
            "observation times are contradictory."
        )
    )


class RadiationResponse(BaseModel):
    rover: str
    credit: str
    citation: str
    source_url: str
    updated_at: UtcDateTime | None
    source_max_sol: int | None
    source_release: date | None
    items: list[RadiationSol]


class Stat3(BaseModel):
    min: float
    max: float
    mean: float


class UvStats(BaseModel):
    max: float
    mean: float


class WindStats(BaseModel):
    speed_mean_ms: float
    gust_ms: float
    direction_mode_deg: float | None = Field(
        description=(
            "Most frequent direction: azimuth in degrees clockwise from north as the source "
            "gives it (MEDA does not say whether it is where the wind blows from or to); "
            "centre of a 22.5 degree sector."
        )
    )


class WeatherFlag(BaseModel):
    code: str
    parameter: str | None
    count: int | None


class ParameterInfo(BaseModel):
    unit: str
    quality: Literal["measured", "preliminary", "estimate"]


class WeatherSol(BaseModel):
    sol: int
    earth_date: date
    ls: float | None
    coverage: float
    hours_covered: int
    air_temp_c: Stat3 | None
    pressure_pa: Stat3 | None
    humidity_pct: Stat3 | None
    wind: WindStats | None = Field(
        default=None,
        description="Perseverance only (absent for Curiosity); null outside sols 36-313.",
    )
    ground_temp_c: Stat3 | None = Field(
        default=None,
        description=(
            "Curiosity (REMS, measured); Perseverance (estimate from the MEDA TIRS brightness "
            "temperature, emissivity 1; absent when switched off)."
        ),
    )
    uv_wm2: UvStats | None = Field(
        default=None, description="Curiosity only (the key is absent for Perseverance)."
    )
    flags: list[WeatherFlag]
    source_version: dict[str, str]


class WeatherHour(BaseModel):
    """One hour of LMST: the mean of each parameter and the lowest and highest valid value."""

    hour: int
    coverage: float
    air_temp_c: float | None
    air_temp_c_min: float | None
    air_temp_c_max: float | None
    pressure_pa: float | None
    pressure_pa_min: float | None
    pressure_pa_max: float | None
    humidity_pct: float | None
    humidity_pct_min: float | None
    humidity_pct_max: float | None
    wind_speed_ms: float | None = Field(default=None, description="Perseverance only.")
    wind_gust_ms: float | None = Field(
        default=None,
        description="Perseverance only. 99th percentile of the wind speed of the hour.",
    )
    wind_direction_deg: float | None = Field(
        default=None,
        description=(
            "Perseverance only. Azimuth in degrees clockwise from north as the source gives it "
            "(not stated whether from or to); centre of a 22.5 degree sector."
        ),
    )
    ground_temp_c: float | None = Field(default=None, description="Curiosity (see WeatherSol).")
    ground_temp_c_min: float | None = Field(default=None, description="See ground_temp_c.")
    ground_temp_c_max: float | None = Field(default=None, description="See ground_temp_c.")
    uv_wm2: float | None = Field(default=None, description="Curiosity only.")
    uv_wm2_min: float | None = Field(default=None, description="Curiosity only.")
    uv_wm2_max: float | None = Field(default=None, description="Curiosity only.")


class WeatherSourceFile(BaseModel):
    product: str
    version: str
    file: str
    link: str


class WeatherSource(BaseModel):
    files: list[WeatherSourceFile]


class WeatherSolDetail(WeatherSol):
    rover: str
    credit: str
    hours: list[WeatherHour]
    source: WeatherSource


class WeatherResponse(BaseModel):
    rover: str
    credit: str
    source_url: str
    updated_at: UtcDateTime | None
    source_max_sol: int | None
    parameters: dict[str, ParameterInfo]
    items: list[WeatherSol]


class WaypointOut(BaseModel):
    sol: int
    site: int
    drive: int
    lat: float
    lon: float
    elev_geoid: float | None
    easting: float | None
    northing: float | None
    roll: float | None
    pitch: float | None
    yaw: float | None
    tilt: float | None
    dist_m: float | None
    dist_total_m: float | None


class LocationResponse(BaseModel):
    rover: str
    available: bool
    updated_at: UtcDateTime | None
    source_max_sol: int | None
    items: list[WaypointOut]


class LineStringOut(BaseModel):
    type: Literal["LineString"] = "LineString"
    coordinates: list[list[float]]


class TraverseProperties(BaseModel):
    sol: int | None
    length_m: float | None


class TraverseFeatureOut(BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: LineStringOut
    properties: TraverseProperties


class TraverseResponse(BaseModel):
    """GeoJSON FeatureCollection (RFC 7946) with extra top-level members."""

    type: Literal["FeatureCollection"] = "FeatureCollection"
    rover: str
    available: bool
    updated_at: UtcDateTime | None
    source_max_sol: int | None
    features: list[TraverseFeatureOut]


class NearestSolsOut(BaseModel):
    before: int | None
    after: int | None


class PhotosResponse(BaseModel):
    items: list[PhotoOut]
    page: int
    per_page: int
    total: int
    nearest_sols: NearestSolsOut | None


class PhotoListResponse(BaseModel):
    items: list[PhotoOut]
    page: int
    per_page: int
    total: int


class PhotoBatchResponse(BaseModel):
    items: list[PhotoOut]
    missing: list[int]


class PhotoStatsGroupOut(BaseModel):
    rover: str
    camera: str | None
    period: str | None
    total_photos: int
    sols_with_photos: int
    first_sol: int
    last_sol: int
    first_date: date
    last_date: date


class PhotoStatsResponse(BaseModel):
    group_by: Literal["rover", "camera", "year", "month"]
    total_photos: int
    sols_with_photos: int
    first_date: date | None
    last_date: date | None
    groups: list[PhotoStatsGroupOut]


class LatestPhotosResponse(BaseModel):
    sol: int | None
    items: list[PhotoOut]
    page: int
    per_page: int
    total: int


class ManifestSolOut(BaseModel):
    sol: int
    earth_date: date
    total_photos: int
    cameras: list[str]


class ManifestResponse(BaseModel):
    rover: RoverOut
    sols: list[ManifestSolOut]


# --- JSON bodies ---------------------------------------------------------------------------
# Lists, the manifest and rovers are returned as plain dicts serialized without
# re-validation (app.api.responses). Keys follow the order of the models above, which stay
# the documented (OpenAPI) shape; datetimes are formatted here with ``_utc_z``.

Json = dict[str, Any]

ALWAYS_FIELDS = frozenset({"id", "credit"})


def _utc_z_or_none(value: datetime | None) -> str | None:
    return _utc_z(value) if value is not None else None


def rover_dict(info: RoverInfo) -> Json:
    rover = info.rover
    return {
        "id": rover.id,
        "name": rover.name,
        "mission": rover.mission,
        "launch_date": rover.launch_date,
        "landing_date": rover.landing_date,
        "end_date": rover.end_date,
        "status": rover.status,
        "max_sol": rover.max_sol,
        "max_date": rover.max_date,
        "total_photos": rover.total_photos,
        "cameras_count": info.cameras_count,
        "last_synced_at": _utc_z_or_none(rover.last_synced_at),
        "data_source": v2.data_source(rover.id),
    }


def camera_out(info: CameraInfo) -> CameraOut:
    return CameraOut(code=info.camera.code, name=info.camera.name, total_photos=info.total_photos)


def _image_urls(photo: Photo, origin: str) -> tuple[str | None, str]:
    """``(small, full)`` URLs; Spirit/Opportunity serve the full frame through our converter.

    Their stored ``img_full`` is the PDS browse preview, so it becomes ``small`` here.
    """
    if photo.rover_id in v2.MER_ROVERS:
        preview = photo.img_small or photo.img_full
        if not photo.source_link:
            return preview, photo.img_full
        full = f"{origin}/api/v2/photos/{photo.id}/image?size=full"
        return preview, full
    return photo.img_small, photo.img_full


def photo_dict(photo: Photo, origin: str, near_stop: NearStop | None = None) -> Json:
    """A Photo of a list: the base keys, then ``local_time`` (from ``extra``, like PhotoDetail)
    and, in a ``near`` search, ``near_stop``."""
    meta = v2.photo_meta(photo)
    item = photo_base_dict(photo, origin, meta)
    item["local_time"] = _local_time_dict(meta.local_time)
    item["mast"] = _mast_dict(meta.mast)
    if near_stop is not None:
        item["near_stop"] = _near_stop_dict(near_stop)
    return item


def _mast_dict(value: MastAngles | None) -> Json | None:
    if value is None:
        return None
    return {"azimuth": value.azimuth, "elevation": value.elevation}


def select_fields(item: Json, fields: frozenset[str] | None) -> Json:
    """Keep the chosen keys of a Photo (``id`` and ``credit`` always), in Photo order."""
    if fields is None:
        return item
    keep = fields | ALWAYS_FIELDS
    return {key: value for key, value in item.items() if key in keep}


def _local_time_dict(value: LocalTime | None) -> Json | None:
    if value is None:
        return None
    return {"time": value.time, "kind": value.kind, "planet": "mars"}


def _near_stop_dict(stop: NearStop) -> Json:
    return {
        "sol": stop.sol,
        "site": stop.site,
        "drive": stop.drive,
        "lat": stop.lat,
        "lon": stop.lon,
        "distance_km": stop.distance_km,
    }


def photo_base_dict(photo: Photo, origin: str, meta: PhotoMeta | None = None) -> Json:
    small, full = _image_urls(photo, origin)
    meta = meta or v2.photo_meta(photo)
    return {
        "id": photo.id,
        "rover": photo.rover_id,
        "sol": photo.sol,
        "earth_date": photo.earth_date,
        "taken_at": _utc_z(photo.taken_at),
        "camera": {"code": photo.camera.code, "name": photo.camera.name},
        "sample_type": photo.sample_type,
        "title": photo.title,
        "img_src": full,
        "images": {
            "small": small,
            "medium": photo.img_medium,
            "large": photo.img_large,
            "full": full,
        },
        "credit": photo.credit,
        "source": {"id": photo.source_id, "link": photo.source_link},
        "width": meta.width,
        "height": meta.height,
    }


def photo_location_out(location: PhotoLocation | None) -> PhotoLocationOut | None:
    if location is None:
        return None
    wp = location.waypoint
    return PhotoLocationOut(
        sol=wp.sol,
        site=wp.site,
        drive=wp.drive,
        lat=wp.lat,
        lon=wp.lon,
        elev_geoid=wp.elev_geoid,
        exact=location.exact,
    )


def photo_detail_out(detail: PhotoDetail, origin: str) -> PhotoDetailOut:
    photo = detail.photo
    meta = v2.photo_meta(photo)
    return PhotoDetailOut(
        **photo_base_dict(photo, origin, meta),
        caption=photo.caption,
        received_at=photo.received_at,
        site=photo.site,
        drive=photo.drive,
        extra=photo.extra or {},
        location=photo_location_out(detail.location),
        local_time=_local_time_out(meta.local_time),
        mast=_mast_out(meta.mast),
        in_sol=_in_sol_out(detail.in_sol),
        flight=detail.flight,
        samples=detail.samples,
    )


def flight_dict(flight: IngenuityFlight, total_photos: int) -> Json:
    """Numerics are sent as JSON numbers (``Decimal`` columns become floats)."""
    return {
        "flight": flight.flight,
        "sol": flight.sol,
        "date": flight.date,
        "distance_m": flight.distance_m,
        "max_altitude_m": flight.max_altitude_m,
        "max_groundspeed_mps": float(flight.max_groundspeed_mps),
        "duration_s": float(flight.duration_s),
        "from": flight.route_from,
        "to": flight.route_to,
        "total_photos": total_photos,
    }


def flights_body(items: list[tuple[IngenuityFlight, int]]) -> Json:
    return {"items": [flight_dict(flight, total) for flight, total in items]}


def sample_location_dict(location: SampleLocation | None) -> Json | None:
    if location is None:
        return None
    wp = location.waypoint
    return {
        "sol": wp.sol,
        "site": wp.site,
        "drive": wp.drive,
        "lat": wp.lat,
        "lon": wp.lon,
        "elev_geoid": wp.elev_geoid,
        "match": location.match,
    }


def sample_dict(view: SampleView) -> Json:
    """Numerics are sent as JSON numbers (``height_cm`` is a ``Decimal`` column)."""
    sample: RoverSample = view.sample
    return {
        "number": sample.number,
        "name": sample.name,
        "feature_name": sample.feature_name,
        "sampling_location": sample.sampling_location,
        "status": sample.status,
        "current_location": sample.current_location,
        "sample_type": sample.sample_type,
        "sample_type_label": sample.sample_type_label,
        "rock_type": sample.rock_type,
        "sealed_on": sample.sealed_on,
        "sol": sample.sol,
        "height_cm": None if sample.height_cm is None else float(sample.height_cm),
        "height_note": sample.height_note,
        "dossier_url": sample.dossier_url,
        "total_photos": view.total_photos,
        "location": sample_location_dict(view.location),
    }


def samples_body(data: SamplesData, source_url: str) -> Json:
    return {
        "rover": data.rover,
        "source_url": source_url,
        "source_updated_at": _utc_z_or_none(data.source_modified_at),
        "updated_at": _utc_z_or_none(data.updated_at),
        "items": [sample_dict(view) for view in data.items],
    }


def _dose_day(mean: float, low: float, high: float) -> Json:
    """uGy/h of the store as uGy/day, to 0.01."""
    return {"mean": round(mean * 24, 2), "min": round(low * 24, 2), "max": round(high * 24, 2)}


def radiation_dict(row: Any, source_url: str) -> Json:
    """One summary: doses x24, the rate of detector B per second of live time, the coverage."""
    return {
        "sol": row.sol,
        "earth_date": row.earth_date,
        "dose_b_ugy_day": _dose_day(row.dose_b_mean, row.dose_b_min, row.dose_b_max),
        "dose_e_ugy_day": _dose_day(row.dose_e_mean, row.dose_e_min, row.dose_e_max),
        "count_rate_b_per_s": round(row.bu_count / row.live_s, 3),
        "coverage": round(min(1.0, row.live_s / SOL_SECONDS), 2),
        "observations": {
            "used": row.n_used,
            "dropped_qc": row.n_dropped_qc,
            "dropped_low_rate": row.n_dropped_low_rate,
            "dropped_invalid": row.n_dropped_invalid,
        },
        "timing_caveat": row.timing_caveat,
        "source": {"file": row.source_path, "link": source_url + row.source_path},
    }


def radiation_body(data: RadiationData, source_url: str) -> Json:
    """``source_url`` is the address of the archive volume (with a trailing slash)."""
    return {
        "rover": data.rover,
        "credit": RAD_CREDIT,
        "citation": RAD_CITATION,
        "source_url": source_url,
        "updated_at": _utc_z_or_none(data.updated_at),
        "source_max_sol": data.source_max_sol,
        "source_release": data.source_release,
        "items": [radiation_dict(row, source_url) for row in data.items],
    }


def radiation_detail_body(data: RadiationSolData, source_url: str) -> Json:
    """One sol: the fields of ``RadiationSol`` and the 24 hours (``None`` when not available)."""
    return {
        "rover": data.rover,
        **radiation_dict(data.row, source_url),
        "credit": RAD_CREDIT,
        "citation": RAD_CITATION,
        "hours": data.hours,
    }


def weather_body(data: WeatherData) -> Json:
    """The list answer; the items are made (and the keys chosen per rover) by the service."""
    return {
        "rover": data.rover,
        "credit": data.credit,
        "source_url": data.source_url,
        "updated_at": _utc_z_or_none(data.updated_at),
        "source_max_sol": data.source_max_sol,
        "parameters": data.parameters,
        "items": data.items,
    }


def weather_detail_body(detail: WeatherDetail) -> Json:
    return {
        "rover": detail.rover,
        **detail.item,
        "credit": detail.credit,
        "hours": detail.hours,
        "source": {"files": detail.files},
    }


def _local_time_out(value: LocalTime | None) -> LocalTimeOut | None:
    return (
        LocalTimeOut(time=value.time, kind=value.kind, planet="mars") if value is not None else None
    )


def _mast_out(value: MastAngles | None) -> MastOut | None:
    return MastOut(azimuth=value.azimuth, elevation=value.elevation) if value else None


def _in_sol_out(value: SolPosition) -> InSolOut:
    return InSolOut(
        position=value.position,
        total=value.total,
        newer_id=value.newer_id,
        older_id=value.older_id,
    )


def _nearest_sols_dict(value: NearestSols | None) -> Json | None:
    if value is None:
        return None
    return {"before": value.before, "after": value.after}


def _photo_items(
    result: PhotoPage, origin: str, fields: frozenset[str] | None = None
) -> list[Json]:
    stops = result.near_stops
    if stops is None:
        return [select_fields(photo_dict(photo, origin), fields) for photo in result.items]
    return [
        select_fields(photo_dict(photo, origin, stops[photo.id]), fields) for photo in result.items
    ]


def photos_body(result: PhotoPage, origin: str, fields: frozenset[str] | None = None) -> Json:
    return {
        "items": _photo_items(result, origin, fields),
        "page": result.page,
        "per_page": result.per_page,
        "total": result.total,
        "nearest_sols": _nearest_sols_dict(result.nearest_sols),
    }


def photo_list_body(result: PhotoPage, origin: str, fields: frozenset[str] | None = None) -> Json:
    """List of photos without ``nearest_sols`` (GET /photos)."""
    return {
        "items": _photo_items(result, origin, fields),
        "page": result.page,
        "per_page": result.per_page,
        "total": result.total,
    }


def photo_batch_body(
    photos: list[Photo], missing: list[int], origin: str, fields: frozenset[str] | None = None
) -> Json:
    items = [select_fields(photo_dict(photo, origin), fields) for photo in photos]
    return {"items": items, "missing": missing}


def photo_stats_body(stats: v2.PhotoStats) -> Json:
    return {
        "group_by": stats.group_by,
        "total_photos": stats.total_photos,
        "sols_with_photos": stats.sols_with_photos,
        "first_date": stats.first_date,
        "last_date": stats.last_date,
        "groups": [
            {
                "rover": g.rover_id,
                "camera": g.camera,
                "period": g.period,
                "total_photos": g.total_photos,
                "sols_with_photos": g.sols_with_photos,
                "first_sol": g.first_sol,
                "last_sol": g.last_sol,
                "first_date": g.first_date,
                "last_date": g.last_date,
            }
            for g in stats.groups
        ],
    }


def latest_body(result: LatestPhotos, origin: str, fields: frozenset[str] | None = None) -> Json:
    page = result.photos
    return {
        "sol": result.sol,
        "items": [select_fields(photo_dict(photo, origin), fields) for photo in page.items],
        "page": page.page,
        "per_page": page.per_page,
        "total": page.total,
    }


_WAYPOINT_FIELDS = (
    "sol",
    "site",
    "drive",
    "lat",
    "lon",
    "elev_geoid",
    "easting",
    "northing",
    "roll",
    "pitch",
    "yaw",
    "tilt",
    "dist_m",
    "dist_total_m",
)


def location_body(result: LocationData) -> Json:
    return {
        "rover": result.rover,
        "available": result.available,
        "updated_at": _utc_z_or_none(result.updated_at),
        "source_max_sol": result.source_max_sol,
        "items": [{name: getattr(wp, name) for name in _WAYPOINT_FIELDS} for wp in result.items],
    }


def traverse_body(result: TraverseData) -> Json:
    return {
        "type": "FeatureCollection",
        "rover": result.rover,
        "available": result.available,
        "updated_at": _utc_z_or_none(result.updated_at),
        "source_max_sol": result.source_max_sol,
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": coordinates},
                "properties": {"sol": sol, "length_m": length_m},
            }
            for sol, length_m, coordinates in result.segments
        ],
    }


def _manifest_sol(summary: SolSummary) -> Json:
    return {
        "sol": summary.sol,
        "earth_date": summary.earth_date,
        "total_photos": summary.total_photos,
        "cameras": list(summary.cameras),
    }


def manifest_body(result: Manifest) -> Json:
    return {"rover": rover_dict(result.rover), "sols": [_manifest_sol(s) for s in result.sols]}


class SyncRequestIn(BaseModel):
    """POST /admin/sync body; integers are strict (no "5", 5.0 or true)."""

    model_config = ConfigDict(extra="ignore")

    rover: StrictStr
    from_sol: Annotated[StrictInt | None, Field(ge=0, le=MAX_SOL)] = None
    to_sol: Annotated[StrictInt | None, Field(ge=0, le=MAX_SOL)] = None


class SyncStartedOut(BaseModel):
    run_id: int
    status: str


class RunOut(BaseModel):
    id: int
    rover: str
    mode: str
    from_sol: int | None
    to_sol: int | None
    status: str
    started_at: UtcDateTime
    finished_at: UtcDateTime | None
    photos_upserted: int
    failed_sols: list[int]
    error: str | None


class RunsResponse(BaseModel):
    items: list[RunOut]
    page: int
    per_page: int
    total: int


def run_out(run: SyncRun) -> RunOut:
    return RunOut(
        id=run.id,
        rover=run.rover_id,
        mode=run.mode,
        from_sol=run.from_sol,
        to_sol=run.to_sol,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        photos_upserted=run.photos_upserted,
        failed_sols=sorted(run.failed_sols or []),
        error=run.error,
    )


def runs_out(result: RunPage) -> RunsResponse:
    return RunsResponse(
        items=[run_out(run) for run in result.items],
        page=result.page,
        per_page=result.per_page,
        total=result.total,
    )


class IssueKeyIn(BaseModel):
    """POST /admin/accounts body."""

    model_config = ConfigDict(extra="ignore")

    email: StrictStr
    note: Annotated[StrictStr | None, Field(max_length=MAX_TEXT_LENGTH)] = None

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        return normalize_email(value)


class BlockAccountIn(BaseModel):
    """POST /admin/accounts/{id}/block body (an empty body means no reason)."""

    model_config = ConfigDict(extra="ignore")

    reason: Annotated[StrictStr | None, Field(max_length=MAX_TEXT_LENGTH)] = None


class ApiKeyInfoOut(BaseModel):
    id: int
    prefix: str
    created_at: UtcDateTime
    last_used_at: UtcDateTime | None
    revoked_at: UtcDateTime | None


AccountStatus = Literal["active", "blocked"]
RevokeReason = Literal["owner", "admin", "replaced"]


class AdminKeyOut(ApiKeyInfoOut):
    revoke_reason: RevokeReason | None


class IssuedAccountOut(BaseModel):
    email: str
    created_at: UtcDateTime


class IssuedKeyOut(BaseModel):
    """The only response that carries the key itself."""

    key: str
    key_info: ApiKeyInfoOut
    account: IssuedAccountOut


class AdminAccountOut(BaseModel):
    id: int
    email: str
    status: AccountStatus
    created_at: UtcDateTime
    verified_at: UtcDateTime | None
    blocked_at: UtcDateTime | None
    block_reason: str | None
    active_keys: int
    requests_30d: int


class UsageDayOut(BaseModel):
    day: date
    requests: int
    rejected: int


class AdminAccountDetailOut(AdminAccountOut):
    keys: list[AdminKeyOut]
    usage_daily: list[UsageDayOut]


class AdminAccountsResponse(BaseModel):
    items: list[AdminAccountOut]
    page: int
    per_page: int
    total: int


def key_info_out(key: ApiKey) -> ApiKeyInfoOut:
    return ApiKeyInfoOut(
        id=key.id,
        prefix=key.prefix,
        created_at=key.created_at,
        last_used_at=key.last_used_at,
        revoked_at=key.revoked_at,
    )


def admin_key_out(key: ApiKey) -> AdminKeyOut:
    reason = cast(RevokeReason | None, key.revoke_reason)
    return AdminKeyOut(**key_info_out(key).model_dump(), revoke_reason=reason)


def issued_key_out(issued: IssuedKey) -> IssuedKeyOut:
    return IssuedKeyOut(
        key=issued.key,
        key_info=key_info_out(issued.record),
        account=IssuedAccountOut(email=issued.account.email, created_at=issued.account.created_at),
    )


def admin_account_out(summary: AccountSummary) -> AdminAccountOut:
    account = summary.account
    return AdminAccountOut(
        id=account.id,
        email=account.email,
        status=cast(AccountStatus, account.status),
        created_at=account.created_at,
        verified_at=account.verified_at,
        blocked_at=account.blocked_at,
        block_reason=account.block_reason,
        active_keys=summary.active_keys,
        requests_30d=summary.requests_30d,
    )


def admin_account_detail_out(detail: AccountDetail) -> AdminAccountDetailOut:
    return AdminAccountDetailOut(
        **admin_account_out(detail.summary).model_dump(),
        keys=[admin_key_out(key) for key in detail.keys],
        usage_daily=[UsageDayOut(day=d, requests=r, rejected=j) for d, r, j in detail.usage_daily],
    )


def admin_accounts_out(page: AccountPage) -> AdminAccountsResponse:
    return AdminAccountsResponse(
        items=[admin_account_out(item) for item in page.items],
        page=page.page,
        per_page=page.per_page,
        total=page.total,
    )
