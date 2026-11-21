"""v2 response models (docs/API.md, "Объекты"); field order is the JSON key order."""

from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, StrictInt, StrictStr

from app.api.v2.params import MAX_SOL
from app.db.models import IngenuityFlight, Photo, RoverSample, SolSummary, SyncRun
from app.services import v2
from app.services.admin import RunPage
from app.services.locations import LocationData, PhotoLocation, TraverseData
from app.services.samples import SampleLocation, SamplesData, SampleView
from app.services.v2 import (
    CameraInfo,
    LatestPhotos,
    LocalTime,
    Manifest,
    NearestSols,
    PhotoDetail,
    PhotoMeta,
    PhotoPage,
    RoverInfo,
    SolPosition,
)


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


class PhotoOut(BaseModel):
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


class PhotoLocationOut(BaseModel):
    sol: int
    site: int
    drive: int
    lat: float
    lon: float
    elev_geoid: float | None
    # True: the photo was taken at this waypoint; False: later (the last waypoint before it).
    exact: bool


class LocalTimeOut(BaseModel):
    time: str
    kind: Literal["lmst", "ltst"]


class InSolOut(BaseModel):
    position: int | None
    total: int
    newer_id: int | None
    older_id: int | None


class PhotoDetailOut(PhotoOut):
    caption: str | None
    received_at: UtcDateTime | None
    site: int | None
    drive: int | None
    extra: dict[str, Any]
    location: PhotoLocationOut | None
    local_time: LocalTimeOut | None
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


def photo_dict(photo: Photo, origin: str, meta: PhotoMeta | None = None) -> Json:
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
        **photo_dict(photo, origin, meta),
        caption=photo.caption,
        received_at=photo.received_at,
        site=photo.site,
        drive=photo.drive,
        extra=photo.extra or {},
        location=photo_location_out(detail.location),
        local_time=_local_time_out(meta.local_time),
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


def _local_time_out(value: LocalTime | None) -> LocalTimeOut | None:
    return LocalTimeOut(time=value.time, kind=value.kind) if value is not None else None


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


def photos_body(result: PhotoPage, origin: str) -> Json:
    return {
        "items": [photo_dict(photo, origin) for photo in result.items],
        "page": result.page,
        "per_page": result.per_page,
        "total": result.total,
        "nearest_sols": _nearest_sols_dict(result.nearest_sols),
    }


def photo_list_body(result: PhotoPage, origin: str) -> Json:
    """List of photos without ``nearest_sols`` (GET /photos)."""
    return {
        "items": [photo_dict(photo, origin) for photo in result.items],
        "page": result.page,
        "per_page": result.per_page,
        "total": result.total,
    }


def latest_body(result: LatestPhotos, origin: str) -> Json:
    page = result.photos
    return {
        "sol": result.sol,
        "items": [photo_dict(photo, origin) for photo in page.items],
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
