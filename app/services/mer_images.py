"""Full-size Spirit/Opportunity frames: PDS3 ``.img`` -> 8-bit JPEG, with a disk cache.

``MerImageService.get_full_jpeg`` serves from the cache, else downloads the product through the
shared upstream client, converts it in a worker thread and caches the JPEG. At most
``MER_CONVERT_CONCURRENCY`` conversions run at once and concurrent requests for one photo share
a single conversion. Errors are never cached.
"""

import asyncio
import hashlib
import io
import logging
import os
import re
import threading
import uuid
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from app.core.config import Settings
from app.core.errors import ConverterBusy, UpstreamTimeout, UpstreamUnavailable
from app.db.models import Photo
from app.sources.http import UpstreamClient, UpstreamError
from app.sources.pds3 import Pds3Error, parse_pds3_image

logger = logging.getLogger(__name__)

DEFAULT_LOW_PERCENTILE = 0.5
DEFAULT_HIGH_PERCENTILE = 99.5
JPEG_QUALITY = 90
# A MER EDR is ~2 MB (1024 x 1024 x 16 bit); more than this is not a frame we convert.
MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024

_KEY_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_SUFFIX = ".jpg"
_TMP_MARK = ".tmp-"


def stretch_to_8bit(
    pixels: NDArray[np.number],
    low_pct: float = DEFAULT_LOW_PERCENTILE,
    high_pct: float = DEFAULT_HIGH_PERCENTILE,
) -> NDArray[np.uint8]:
    """Linear stretch between the percentiles of the frame to 0..255, clipped.

    A flat frame (both percentiles equal) becomes a uniform image, not a division by zero.
    """
    values = np.asarray(pixels, dtype=np.float32)
    low, high = (float(v) for v in np.percentile(values, [low_pct, high_pct]))
    if not high > low:
        return np.zeros(values.shape, dtype=np.uint8)
    scaled = (values - np.float32(low)) * np.float32(255.0 / (high - low))
    return np.rint(np.clip(scaled, 0.0, 255.0)).astype(np.uint8)


def encode_jpeg(pixels_u8: NDArray[np.uint8], quality: int = JPEG_QUALITY) -> bytes:
    """Grayscale JPEG of a 2D uint8 array, without metadata."""
    buffer = io.BytesIO()
    Image.fromarray(pixels_u8, mode="L").save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


def convert_pds3_to_jpeg(data: bytes) -> bytes:
    """CPU-bound conversion of a whole ``.img`` file; ``Pds3Error`` if it is not an image."""
    image = parse_pds3_image(data)
    return encode_jpeg(stretch_to_8bit(image.pixels))


class ImageDiskCache:
    """JPEG files by key in one directory, bounded by ``max_bytes``.

    ``get`` refreshes the file's modification time (the "last used" mark); ``put`` writes
    atomically (temp file + replace) and then removes the least recently used files until the
    total fits. Disk errors are logged and never raised: the cache just stays empty. The
    methods are synchronous, call them from a worker thread.
    """

    def __init__(self, directory: Path, max_bytes: int) -> None:
        self._dir = Path(directory)
        self._max_bytes = max_bytes
        self._lock = threading.Lock()
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            for orphan in self._dir.glob(f".*{_TMP_MARK}*"):
                orphan.unlink(missing_ok=True)
        except OSError:
            logger.exception("Cannot prepare the image cache directory %s", self._dir)

    def _path(self, key: str) -> Path | None:
        return self._dir / f"{key}{_SUFFIX}" if _KEY_RE.fullmatch(key) else None

    def get(self, key: str) -> bytes | None:
        path = self._path(key)
        if path is None:
            return None
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError:
            logger.exception("Cannot read cached image %s", key)
            return None
        try:
            os.utime(path)
        except OSError:
            logger.warning("Cannot touch cached image %s", key)
        return data

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        if path is None:
            return
        tmp = self._dir / f".{key}{_TMP_MARK}{uuid.uuid4().hex}"
        try:
            with self._lock:
                self._dir.mkdir(parents=True, exist_ok=True)
                tmp.write_bytes(data)
                tmp.replace(path)
                self._evict(keep=path)
        except OSError:
            logger.exception("Cannot cache image %s", key)
            tmp.unlink(missing_ok=True)

    def _evict(self, keep: Path) -> None:
        """Delete least recently used files until the total is within the limit."""
        entries: list[tuple[float, int, Path]] = []
        total = 0
        with os.scandir(self._dir) as it:
            for entry in it:
                if not entry.name.endswith(_SUFFIX) or entry.name.startswith("."):
                    continue
                try:
                    stat = entry.stat()
                except OSError:
                    continue
                total += stat.st_size
                entries.append((stat.st_mtime, stat.st_size, Path(entry.path)))
        for _mtime, size, path in sorted(entries):
            if total <= self._max_bytes:
                break
            if path == keep:
                continue
            try:
                path.unlink()
            except OSError:
                logger.warning("Cannot evict cached image %s", path.name)
                continue
            total -= size


def cache_key(photo: Photo) -> str:
    """``{id}-{sha256(source_link)[:16]}``: a re-imported database that reuses an id for another
    frame gets another key, so a stale image is never served."""
    digest = hashlib.sha256((photo.source_link or "").encode()).hexdigest()[:16]
    return f"{photo.id}-{digest}"


class MerImageService:
    """Serves converted full-size JPEGs of Spirit/Opportunity photos.

    Owns its upstream client (own rate limit ``MER_PDS_MAX_RPS``, not shared with the sync
    client); ``aclose()`` cancels running work and closes it. At most ``MER_CONVERT_CONCURRENCY``
    frames run and ``MER_CONVERT_QUEUE`` more wait; further new frames raise ``ConverterBusy``.
    """

    def __init__(
        self,
        settings: Settings,
        client: UpstreamClient | None = None,
        cache: ImageDiskCache | None = None,
    ) -> None:
        self._client = client or UpstreamClient(
            settings.model_copy(update={"upstream_max_rps": settings.mer_pds_max_rps})
        )
        self._timeout = settings.mer_convert_timeout_seconds
        self._allowed_origin = settings.mer_pds_base_url
        self._cache = cache or ImageDiskCache(
            settings.mer_image_cache_dir, settings.mer_image_cache_max_mb * 1024 * 1024
        )
        self._capacity = settings.mer_convert_concurrency + settings.mer_convert_queue
        self._semaphore = asyncio.Semaphore(settings.mer_convert_concurrency)
        self._inflight: dict[str, asyncio.Task[bytes]] = {}

    async def aclose(self) -> None:
        """Cancel in-flight conversions and close the upstream client."""
        tasks = list(self._inflight.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self._client.aclose()

    async def get_full_jpeg(self, photo: Photo) -> bytes:
        """The full frame as JPEG bytes.

        Raises ``UpstreamUnavailable`` (502), ``UpstreamTimeout`` (504) or ``ConverterBusy``
        (503). The timeout only bounds this caller's wait: a conversion thread cannot be
        cancelled and keeps its slot until it ends.
        """
        key = cache_key(photo)
        try:
            async with asyncio.timeout(self._timeout):
                cached = await asyncio.to_thread(self._cache.get, key)
                if cached is not None:
                    return cached
                return await asyncio.shield(self._shared_task(key, photo))
        except TimeoutError:
            logger.warning("Conversion of photo %s timed out", photo.id)
            raise UpstreamTimeout() from None

    def _shared_task(self, key: str, photo: Photo) -> asyncio.Task[bytes]:
        task = self._inflight.get(key)
        if task is None:
            if len(self._inflight) >= self._capacity:
                raise ConverterBusy()
            task = asyncio.create_task(self._produce(key, photo.source_link))
            self._inflight[key] = task
            task.add_done_callback(lambda done: self._finished(key, done))
        return task

    def _finished(self, key: str, task: asyncio.Task[bytes]) -> None:
        if self._inflight.get(key) is task:
            del self._inflight[key]
        if not task.cancelled():
            task.exception()  # mark retrieved: every waiter may have gone already

    async def _produce(self, key: str, url: str | None) -> bytes:
        """Wait for a slot, download, convert and cache one frame."""
        if not url:
            logger.warning("Photo %s has no source link", key)
            raise UpstreamUnavailable()
        try:
            async with asyncio.timeout(self._timeout):
                await self._semaphore.acquire()
        except TimeoutError:
            raise UpstreamTimeout() from None
        try:
            data = await self._download(key, url)
        except BaseException:
            self._semaphore.release()
            raise
        jpeg = await self._convert(key, data)
        await asyncio.to_thread(self._cache.put, key, jpeg)
        return jpeg

    async def _download(self, key: str, url: str) -> bytes:
        try:
            async with asyncio.timeout(self._timeout):
                return await self._client.get_bytes(
                    url, max_bytes=MAX_DOWNLOAD_BYTES, allowed_origin=self._allowed_origin
                )
        except TimeoutError:
            raise UpstreamTimeout() from None
        except UpstreamError as exc:
            logger.warning("Download for photo %s failed: %s", key, exc)
            raise UpstreamUnavailable() from None

    async def _convert(self, key: str, data: bytes) -> bytes:
        """Convert in a thread; the slot is released only when the thread has finished."""
        work = asyncio.ensure_future(asyncio.to_thread(convert_pds3_to_jpeg, data))
        work.add_done_callback(self._release_slot)
        try:
            return await asyncio.shield(work)
        except Pds3Error as exc:
            logger.warning("Photo %s is not a readable PDS3 image: %s", key, exc)
            raise UpstreamUnavailable() from None
        except Exception:
            logger.exception("Conversion of photo %s failed", key)
            raise UpstreamUnavailable() from None

    def _release_slot(self, work: asyncio.Future[bytes]) -> None:
        self._semaphore.release()
        if not work.cancelled():
            work.exception()
