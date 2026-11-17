"""v2 response models (docs/API.md, "Объекты"); field order is the JSON key order."""

from datetime import UTC, date, datetime
from typing import Annotated, Any

from pydantic import BaseModel, PlainSerializer

from app.db.models import Photo, SolSummary
from app.services.v2 import CameraInfo, LatestPhotos, Manifest, PhotoPage, RoverInfo


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
    status: str
    max_sol: int | None
    max_date: date | None
    total_photos: int
    cameras_count: int
    last_synced_at: UtcDateTime | None


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


class PhotoDetailOut(PhotoOut):
    caption: str | None
    received_at: UtcDateTime | None
    site: int | None
    drive: int | None
    extra: dict[str, Any]


class PhotosResponse(BaseModel):
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


def rover_out(info: RoverInfo) -> RoverOut:
    rover = info.rover
    return RoverOut(
        id=rover.id,
        name=rover.name,
        mission=rover.mission,
        launch_date=rover.launch_date,
        landing_date=rover.landing_date,
        status=rover.status,
        max_sol=rover.max_sol,
        max_date=rover.max_date,
        total_photos=rover.total_photos,
        cameras_count=info.cameras_count,
        last_synced_at=rover.last_synced_at,
    )


def camera_out(info: CameraInfo) -> CameraOut:
    return CameraOut(code=info.camera.code, name=info.camera.name, total_photos=info.total_photos)


def _photo_fields(photo: Photo) -> dict[str, Any]:
    return {
        "id": photo.id,
        "rover": photo.rover_id,
        "sol": photo.sol,
        "earth_date": photo.earth_date,
        "taken_at": photo.taken_at,
        "camera": PhotoCameraOut(code=photo.camera.code, name=photo.camera.name),
        "sample_type": photo.sample_type,
        "title": photo.title,
        "img_src": photo.img_full,
        "images": ImagesOut(
            small=photo.img_small,
            medium=photo.img_medium,
            large=photo.img_large,
            full=photo.img_full,
        ),
        "credit": photo.credit,
        "source": SourceOut(id=photo.source_id, link=photo.source_link),
    }


def photo_out(photo: Photo) -> PhotoOut:
    return PhotoOut(**_photo_fields(photo))


def photo_detail_out(photo: Photo) -> PhotoDetailOut:
    return PhotoDetailOut(
        **_photo_fields(photo),
        caption=photo.caption,
        received_at=photo.received_at,
        site=photo.site,
        drive=photo.drive,
        extra=photo.extra or {},
    )


def photos_out(result: PhotoPage) -> PhotosResponse:
    return PhotosResponse(
        items=[photo_out(photo) for photo in result.items],
        page=result.page,
        per_page=result.per_page,
        total=result.total,
    )


def latest_out(result: LatestPhotos) -> LatestPhotosResponse:
    page = result.photos
    return LatestPhotosResponse(
        sol=result.sol,
        items=[photo_out(photo) for photo in page.items],
        page=page.page,
        per_page=page.per_page,
        total=page.total,
    )


def _manifest_sol(summary: SolSummary) -> ManifestSolOut:
    return ManifestSolOut(
        sol=summary.sol,
        earth_date=summary.earth_date,
        total_photos=summary.total_photos,
        cameras=list(summary.cameras),
    )


def manifest_out(result: Manifest) -> ManifestResponse:
    return ManifestResponse(
        rover=rover_out(result.rover), sols=[_manifest_sol(s) for s in result.sols]
    )
