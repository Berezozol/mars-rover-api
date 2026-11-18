"""v1 response models (docs/LEGACY-API.md, "Объекты"); field order is the JSON key order."""

from datetime import date

from pydantic import BaseModel

from app.services.legacy import LegacyManifest, LegacyPhotoItem, LegacyRover


class RoverCameraOut(BaseModel):
    name: str
    full_name: str


class RoverOut(BaseModel):
    id: int
    name: str
    landing_date: date
    launch_date: date
    status: str
    max_sol: int | None
    max_date: date | None
    total_photos: int
    cameras: list[RoverCameraOut]


class PhotoCameraOut(BaseModel):
    id: int
    name: str
    rover_id: int
    full_name: str


class PhotoOut(BaseModel):
    id: int
    sol: int
    camera: PhotoCameraOut
    img_src: str
    earth_date: date
    rover: RoverOut


class ManifestSolOut(BaseModel):
    sol: int
    earth_date: date
    total_photos: int
    cameras: list[str]


class ManifestOut(BaseModel):
    name: str
    landing_date: date
    launch_date: date
    status: str
    max_sol: int | None
    max_date: date | None
    total_photos: int
    photos: list[ManifestSolOut]


class RoversResponse(BaseModel):
    rovers: list[RoverOut]


class RoverResponse(BaseModel):
    rover: RoverOut


class PhotosResponse(BaseModel):
    photos: list[PhotoOut]


class LatestPhotosResponse(BaseModel):
    latest_photos: list[PhotoOut]


class PhotoResponse(BaseModel):
    photo: PhotoOut


class ManifestResponse(BaseModel):
    photo_manifest: ManifestOut


def rover_out(item: LegacyRover) -> RoverOut:
    rover = item.rover
    return RoverOut(
        id=rover.legacy_id,
        name=rover.name,
        landing_date=rover.landing_date,
        launch_date=rover.launch_date,
        status=rover.status,
        max_sol=item.counters.max_sol,
        max_date=item.counters.max_date,
        total_photos=item.counters.total_photos,
        cameras=[RoverCameraOut(name=c.code, full_name=c.name) for c in item.cameras],
    )


def photos_out(item: LegacyRover, photos: list[LegacyPhotoItem]) -> list[PhotoOut]:
    """Photos of one rover; the rover object is built once and shared."""
    rover = rover_out(item)
    return [_photo_out(item, rover, photo) for photo in photos]


def photo_out(item: LegacyRover, photo: LegacyPhotoItem) -> PhotoOut:
    return _photo_out(item, rover_out(item), photo)


def _photo_out(item: LegacyRover, rover: RoverOut, entry: LegacyPhotoItem) -> PhotoOut:
    photo = entry.photo
    return PhotoOut(
        id=photo.id,
        sol=photo.sol,
        camera=PhotoCameraOut(
            id=photo.camera.id,
            name=photo.camera.code,
            rover_id=item.rover.legacy_id,
            full_name=photo.camera.name,
        ),
        img_src=entry.img_src,
        earth_date=entry.earth_date,
        rover=rover,
    )


def manifest_out(item: LegacyManifest) -> ManifestOut:
    rover = item.rover
    return ManifestOut(
        name=rover.name,
        landing_date=rover.landing_date,
        launch_date=rover.launch_date,
        status=rover.status,
        max_sol=item.counters.max_sol,
        max_date=item.counters.max_date,
        total_photos=item.counters.total_photos,
        photos=[
            ManifestSolOut(
                sol=entry.sol,
                earth_date=entry.earth_date,
                total_photos=entry.total_photos,
                cameras=entry.cameras,
            )
            for entry in item.sols
        ],
    )
