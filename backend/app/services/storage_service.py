"""
Backblaze B2 storage service — the durable home for the media library.

Flux is B2-first. A render produces several artefacts (mp4, thumbnail, SRT,
script JSON, Genblaze provenance manifest); all of them are uploaded under a
single per-video prefix and served back to the browser as presigned GET URLs.
Local disk is scratch space only, which is what lets Flux run on hosts with an
ephemeral filesystem — the library outlives the container.

Bucket layout::

    {B2_PREFIX}/library/{stem}/video.mp4
                              /thumb.jpg
                              /captions.srt
                              /script.json
                              /manifest.json      <- Genblaze provenance
                              /meta.json          <- library card metadata

Genblaze's own ObjectStorageSink writes its runs alongside this, under
``{B2_PREFIX}/runs/...`` (see genblaze_service).

Every public function degrades gracefully: when B2 is not configured the
service reports ``available == False`` and callers fall back to local disk.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

# Artefact name -> object name inside the per-video prefix.
VIDEO_OBJECT = "video.mp4"
THUMB_OBJECT = "thumb.jpg"
SRT_OBJECT = "captions.srt"
SCRIPT_OBJECT = "script.json"
MANIFEST_OBJECT = "manifest.json"
META_OBJECT = "meta.json"

_CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".srt": "application/x-subrip",
    ".json": "application/json",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
}


def _content_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    return _CONTENT_TYPES.get(suffix) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


class StorageError(RuntimeError):
    """Raised when a required B2 operation fails."""


@dataclass
class LibraryItem:
    """One video in the B2-backed library."""

    stem: str
    name: str
    video_key: str
    created_at: float
    meta: Dict[str, Any] = field(default_factory=dict)
    thumb_key: Optional[str] = None
    srt_key: Optional[str] = None
    manifest_key: Optional[str] = None


class B2StorageService:
    """Thin, lazily-initialized wrapper over the Genblaze S3 backend."""

    def __init__(self) -> None:
        self._backend = None
        self._lock = threading.Lock()
        self._init_error: Optional[str] = None
        # meta.json is immutable once written, so caching it avoids an extra GET
        # per library card on every /videos/list call.
        self._meta_cache: Dict[str, Dict[str, Any]] = {}
        # (key, ttl) -> (url, reuse_until). Keeps presigned URLs stable so the
        # browser can cache the media they point at.
        self._url_cache: Dict[tuple, tuple] = {}

    # -- lifecycle ----------------------------------------------------

    @property
    def available(self) -> bool:
        """True when B2 credentials are present and the client initialized."""
        if not settings.b2_configured:
            return False
        return self.backend is not None

    @property
    def backend(self):
        """The genblaze-s3 S3StorageBackend, or None when unavailable."""
        if self._backend is not None:
            return self._backend
        if not settings.b2_configured or self._init_error:
            return None

        with self._lock:
            if self._backend is not None:
                return self._backend
            try:
                from genblaze_s3 import S3StorageBackend

                self._backend = S3StorageBackend.for_backblaze(
                    settings.B2_BUCKET,
                    key_id=settings.B2_KEY_ID,
                    app_key=settings.B2_APP_KEY,
                    region=settings.B2_REGION or None,
                )
                logger.info("B2 storage ready: bucket=%s prefix=%s",
                            settings.B2_BUCKET, settings.B2_PREFIX)
            except Exception as e:  # noqa: BLE001 - never let storage kill boot
                self._init_error = str(e)
                logger.error("B2 storage unavailable: %s", e)
                return None
        return self._backend

    def status(self) -> Dict[str, Any]:
        """Readiness detail for /health and the UI's storage badge."""
        return {
            "configured": settings.b2_configured,
            "available": self.available,
            "bucket": settings.B2_BUCKET or None,
            "prefix": settings.B2_PREFIX,
            "error": self._init_error,
        }

    # -- keys ---------------------------------------------------------

    def library_prefix(self) -> str:
        return f"{settings.B2_PREFIX.strip('/')}/library"

    def video_prefix(self, stem: str) -> str:
        return f"{self.library_prefix()}/{stem}"

    def key_for(self, stem: str, object_name: str) -> str:
        return f"{self.video_prefix(stem)}/{object_name}"

    # -- write --------------------------------------------------------

    def put_file(self, local_path: Path, key: str) -> Optional[str]:
        """Upload one local file. Returns the key, or None on failure."""
        backend = self.backend
        if backend is None:
            return None
        local_path = Path(local_path)
        if not local_path.exists() or local_path.stat().st_size == 0:
            logger.warning("Skipping upload of missing/empty file: %s", local_path)
            return None
        try:
            with open(local_path, "rb") as fh:
                backend.put(key, fh, content_type=_content_type_for(local_path))
            logger.info("B2 upload ok: %s (%d bytes)", key, local_path.stat().st_size)
            return key
        except Exception as e:  # noqa: BLE001
            logger.error("B2 upload failed for %s -> %s: %s", local_path, key, e)
            return None

    def put_json(self, payload: Dict[str, Any], key: str) -> Optional[str]:
        """Upload a JSON document."""
        backend = self.backend
        if backend is None:
            return None
        try:
            data = json.dumps(payload, indent=2, default=str).encode("utf-8")
            backend.put(key, data, content_type="application/json")
            return key
        except Exception as e:  # noqa: BLE001
            logger.error("B2 JSON upload failed for %s: %s", key, e)
            return None

    def upload_render(
        self,
        stem: str,
        video_path: Path,
        thumbnail_path: Optional[Path] = None,
        srt_path: Optional[Path] = None,
        script_path: Optional[Path] = None,
        manifest: Optional[Dict[str, Any]] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Optional[str]]:
        """
        Upload every artefact of one render under this video's prefix.

        Returns a dict of artefact -> key (None where the artefact was absent or
        the upload failed). The video upload is the only one that matters for
        the library to work; the rest are best-effort enrichment.
        """
        keys: Dict[str, Optional[str]] = {
            "video": None, "thumbnail": None, "srt": None,
            "script": None, "manifest": None, "meta": None,
        }
        if not self.available:
            return keys

        # The five artefact uploads are independent of each other and each one is
        # almost entirely round-trip latency, so run them together. Serially this
        # was the second-largest cost in the pipeline; concurrently it costs about
        # as much as the single slowest upload (the video).
        #
        # meta.json is deliberately NOT in this batch — it is the completion
        # marker, and writing it before the others land would let the library list
        # a video whose captions or thumbnail are still uploading.
        jobs = [("video", lambda: self.put_file(video_path, self.key_for(stem, VIDEO_OBJECT)))]
        if thumbnail_path:
            jobs.append(("thumbnail", lambda: self.put_file(thumbnail_path, self.key_for(stem, THUMB_OBJECT))))
        if srt_path:
            jobs.append(("srt", lambda: self.put_file(srt_path, self.key_for(stem, SRT_OBJECT))))
        if script_path:
            jobs.append(("script", lambda: self.put_file(script_path, self.key_for(stem, SCRIPT_OBJECT))))
        if manifest:
            jobs.append(("manifest", lambda: self.put_json(manifest, self.key_for(stem, MANIFEST_OBJECT))))

        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            futures = {name: pool.submit(job) for name, job in jobs}
            for name, future in futures.items():
                try:
                    keys[name] = future.result()
                except Exception as e:  # noqa: BLE001 - the video check below decides
                    logger.warning("Upload of %s for %s failed: %s", name, stem, e)

        if keys["video"] is None:
            raise StorageError(f"Failed to upload video to B2 for {stem}")

        # meta.json is written last: its presence marks the render as complete.
        record = dict(meta or {})
        record.update({
            "stem": stem,
            "name": f"{stem}.mp4",
            "created_at": record.get("created_at") or time.time(),
            "artefacts": {k: v for k, v in keys.items() if v},
        })
        keys["meta"] = self.put_json(record, self.key_for(stem, META_OBJECT))
        self._meta_cache[stem] = record
        return keys

    # -- read ---------------------------------------------------------

    def presign(self, key: Optional[str], expires_in: Optional[int] = None) -> Optional[str]:
        """
        Presigned GET URL for a key (the bucket itself stays private).

        URLs are cached and reused until they near expiry. Signing is cheap, but
        a *fresh signature every call* produces a different URL every time, which
        defeats the browser cache: the library re-downloads every thumbnail on
        each poll (every 2.5s during a render). That burned through the bucket's
        daily download cap and B2 began returning 403 for all media. Returning a
        stable URL lets the browser cache normally.
        """
        backend = self.backend
        if backend is None or not key:
            return None

        ttl = expires_in or settings.B2_URL_TTL_SECONDS
        now = time.time()
        cached = self._url_cache.get((key, ttl))
        if cached and cached[1] > now:
            return cached[0]

        try:
            url = backend.presigned_get_url(key, expires_in=ttl)
        except Exception as e:  # noqa: BLE001
            logger.error("Presign failed for %s: %s", key, e)
            return None

        # Retire the cached URL well before the signature actually expires, so a
        # page opened just before the boundary still has a usable link.
        self._url_cache[(key, ttl)] = (url, now + max(60, ttl * 0.5))
        return url

    def read_json(self, key: str) -> Optional[Dict[str, Any]]:
        backend = self.backend
        if backend is None:
            return None
        try:
            return json.loads(backend.get(key).decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.debug("B2 read failed for %s: %s", key, e)
            return None

    def _meta_for(self, stem: str) -> Dict[str, Any]:
        if stem in self._meta_cache:
            return self._meta_cache[stem]
        meta = self.read_json(self.key_for(stem, META_OBJECT)) or {}
        if meta:
            self._meta_cache[stem] = meta
        return meta

    def list_library(self) -> List[LibraryItem]:
        """
        Every completed video in the bucket, newest first.

        Built from a single paginated LIST over the library prefix; per-video
        metadata comes from a cached meta.json read.
        """
        backend = self.backend
        if backend is None:
            return []

        prefix = self.library_prefix() + "/"
        grouped: Dict[str, Dict[str, Any]] = {}
        token = None
        try:
            while True:
                page = backend.list(prefix, max_keys=1000, continuation_token=token)
                for entry in page.entries:
                    rel = entry.key[len(prefix):]
                    if "/" not in rel:
                        continue
                    stem, _, object_name = rel.partition("/")
                    bucket = grouped.setdefault(stem, {})
                    bucket[object_name] = entry
                token = page.next_token
                if not token:
                    break
        except Exception as e:  # noqa: BLE001
            logger.error("B2 library listing failed: %s", e)
            return []

        items: List[LibraryItem] = []
        for stem, objects in grouped.items():
            video_entry = objects.get(VIDEO_OBJECT)
            if video_entry is None:
                continue  # partial/failed render — don't surface it
            meta = self._meta_for(stem)
            created = meta.get("created_at")
            if not isinstance(created, (int, float)):
                created = video_entry.last_modified.timestamp()
            items.append(LibraryItem(
                stem=stem,
                name=meta.get("name") or f"{stem}.mp4",
                video_key=video_entry.key,
                created_at=float(created),
                meta=meta,
                thumb_key=objects[THUMB_OBJECT].key if THUMB_OBJECT in objects else None,
                srt_key=objects[SRT_OBJECT].key if SRT_OBJECT in objects else None,
                manifest_key=objects[MANIFEST_OBJECT].key if MANIFEST_OBJECT in objects else None,
            ))

        items.sort(key=lambda i: i.created_at, reverse=True)
        return items

    def get_item(self, stem: str) -> Optional[LibraryItem]:
        """Look up a single library entry by stem."""
        backend = self.backend
        if backend is None:
            return None
        video_key = self.key_for(stem, VIDEO_OBJECT)
        try:
            if not backend.exists(video_key):
                return None
        except Exception as e:  # noqa: BLE001
            logger.error("B2 exists() failed for %s: %s", video_key, e)
            return None

        meta = self._meta_for(stem)
        manifest_key = self.key_for(stem, MANIFEST_OBJECT)
        srt_key = self.key_for(stem, SRT_OBJECT)
        thumb_key = self.key_for(stem, THUMB_OBJECT)
        artefacts = meta.get("artefacts") or {}
        return LibraryItem(
            stem=stem,
            name=meta.get("name") or f"{stem}.mp4",
            video_key=video_key,
            created_at=float(meta.get("created_at") or 0.0),
            meta=meta,
            thumb_key=thumb_key if artefacts.get("thumbnail") else None,
            srt_key=srt_key if artefacts.get("srt") else None,
            manifest_key=manifest_key if artefacts.get("manifest") else None,
        )

    # -- delete -------------------------------------------------------

    def delete_video(self, stem: str) -> bool:
        """Delete every artefact for one video. Returns True if anything went."""
        backend = self.backend
        if backend is None:
            return False
        try:
            result = backend.delete_prefix(self.video_prefix(stem) + "/", dry_run=False)
            self._meta_cache.pop(stem, None)
            # Drop any cached URLs for this video so a deleted item can't be
            # served from cache after the objects are gone.
            prefix = self.video_prefix(stem) + "/"
            for cache_key in [k for k in self._url_cache if k[0].startswith(prefix)]:
                self._url_cache.pop(cache_key, None)
            deleted = getattr(result, "deleted", None)
            count = len(deleted) if isinstance(deleted, (list, tuple)) else (deleted or 0)
            logger.info("Deleted %s objects for %s from B2", count, stem)
            return True
        except Exception as e:  # noqa: BLE001
            logger.error("B2 delete failed for %s: %s", stem, e)
            return False


# Module-level singleton — the S3 client is thread-safe and reused.
storage = B2StorageService()
