"""
Video Generation API Endpoints

The library is Backblaze B2-first: /list, /stream and /download resolve against
the bucket and hand the browser short-lived presigned URLs, falling back to the
local /static directory only for videos rendered before B2 was configured.
"""
import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from app.schemas.video import (
    ProvenanceResponse,
    VideoGenerationRequest,
    VideoGenerationResponse,
    VideoListResponse,
)
from app.services.video_service import VideoGenerationService
from app.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# Initialize video service
video_service = VideoGenerationService()

_VIDEO_SUFFIXES = ('.mp4', '.webm', '.ogg', '.mov', '.avi')


def _safe_name(video_name: str) -> str:
    """Reject path traversal — only a bare filename is ever valid here."""
    safe = os.path.basename(video_name)
    if safe != video_name or not safe:
        raise HTTPException(status_code=400, detail="Invalid video name")
    return safe


@router.post("/generate", response_model=VideoGenerationResponse)
async def generate_video(
    request: VideoGenerationRequest,
    background_tasks: BackgroundTasks
):
    """
    Generate a video based on the provided parameters.
    This endpoint triggers the full video generation pipeline:
    1. Script generation
    2. Image generation (Genblaze -> Pexels -> Gemini)
    3. Audio/TTS generation (Genblaze cloud TTS or local Kokoro)
    4. Subtitle generation
    5. Video assembly
    6. Provenance manifest (Genblaze)
    7. Durable upload to Backblaze B2
    """
    try:
        logger.info(f"Received video generation request for topic: {request.topic}")

        # Generate video (runs in background)
        result = await video_service.generate_video_async(request, background_tasks)

        return result

    except Exception as e:
        logger.error(f"Video generation failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Video generation failed: {str(e)}")


@router.get("/status")
async def render_status():
    """
    Live render status: which pipeline stage is currently running.
    Stage is one of: fetch, trend, script, image, voice, subtitles, assembly,
    provenance, storage, publish, done, error (or null when idle).
    """
    # Imported lazily so this endpoint stays available even before the heavy
    # render dependencies are loaded.
    from app.services.video_service import render_status as status
    return status


@router.get("/storage", response_model=None)
async def storage_status() -> Dict[str, Any]:
    """
    Where media is stored and whether provenance is active.

    Judges (and the UI's status badges) can hit this to confirm the app really
    is talking to Backblaze B2 and Genblaze.
    """
    from app.services.genblaze_service import genblaze
    from app.services.storage_service import storage

    return {
        "backblaze_b2": storage.status(),
        "genblaze": genblaze.status(),
        "keep_local_copy": settings.KEEP_LOCAL_COPY,
    }


@router.get("/list", response_model=List[VideoListResponse])
async def list_videos():
    """
    List generated videos, NEWEST FIRST.

    B2 is the source of truth; any local-only files (rendered before B2 was
    configured, or left behind by KEEP_LOCAL_COPY) are appended so nothing
    silently disappears from the library.
    """
    try:
        from app.services.storage_service import storage

        videos: List[VideoListResponse] = []
        seen: set = set()

        # --- B2-backed entries -------------------------------------------
        if storage.available:
            for item in storage.list_library():
                meta = item.meta or {}
                url = storage.presign(item.video_key)
                if not url:
                    continue
                seen.add(item.name)
                videos.append(VideoListResponse(
                    name=item.name,
                    path=url,
                    thumbnail=storage.presign(item.thumb_key),
                    storage="b2",
                    topic=meta.get("topic"),
                    created_at=item.created_at,
                    duration_sec=meta.get("duration_sec"),
                    verified=bool(meta.get("verified")),
                    manifest_hash=meta.get("manifest_hash"),
                    run_id=meta.get("run_id"),
                    has_provenance=item.manifest_key is not None,
                ))

        # --- Local-only leftovers ----------------------------------------
        video_dir = settings.STATIC_DIR / "videos"
        if video_dir.exists():
            entries = []
            for filename in os.listdir(video_dir):
                if not filename.lower().endswith(_VIDEO_SUFFIXES) or filename in seen:
                    continue
                file_path = video_dir / filename
                try:
                    mtime = file_path.stat().st_mtime
                except OSError:
                    mtime = 0
                entries.append((mtime, filename))

            entries.sort(key=lambda item: item[0], reverse=True)
            for mtime, filename in entries:
                thumb_name = f"{os.path.splitext(filename)[0]}.jpg"
                videos.append(VideoListResponse(
                    name=filename,
                    path=f"/static/videos/{filename}",
                    thumbnail=(f"/static/videos/{thumb_name}"
                               if (video_dir / thumb_name).exists() else None),
                    storage="local",
                    created_at=mtime,
                ))

        videos.sort(key=lambda v: v.created_at or 0, reverse=True)
        return videos

    except Exception as e:
        logger.error(f"Failed to list videos: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list videos: {str(e)}")


@router.get("/download/{video_name}")
async def download_video(video_name: str):
    """
    Download a video. B2-hosted videos redirect to a presigned URL so the bytes
    stream straight from the bucket instead of through this process.
    """
    safe_name = _safe_name(video_name)
    try:
        from app.services.storage_service import storage

        if storage.available:
            item = storage.get_item(os.path.splitext(safe_name)[0])
            if item:
                url = storage.presign(item.video_key)
                if url:
                    return RedirectResponse(url, status_code=307)

        video_path = settings.STATIC_DIR / "videos" / safe_name
        if not video_path.exists():
            raise HTTPException(status_code=404, detail="Video not found")

        return FileResponse(path=video_path, media_type="video/mp4", filename=safe_name)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to download video: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to download video: {str(e)}")


@router.get("/stream/{video_name}")
async def stream_video(video_name: str):
    """
    Stream a video. B2-hosted videos redirect to a presigned URL, which gives
    the browser native range-request seeking served by B2.
    """
    safe_name = _safe_name(video_name)
    try:
        from app.services.storage_service import storage

        if storage.available:
            item = storage.get_item(os.path.splitext(safe_name)[0])
            if item:
                url = storage.presign(item.video_key)
                if url:
                    return RedirectResponse(url, status_code=307)

        video_path = settings.STATIC_DIR / "videos" / safe_name
        if not video_path.exists():
            raise HTTPException(status_code=404, detail="Video not found")

        return FileResponse(path=video_path, media_type="video/mp4")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to stream video: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to stream video: {str(e)}")


@router.get("/{video_name}/provenance", response_model=ProvenanceResponse)
async def video_provenance(video_name: str):
    """
    The Genblaze provenance manifest for one video, re-verified on read.

    Answers "how was this media made?" — provider, model, prompt and parameters
    for every stage, plus the SHA-256 of each artefact and whether the manifest
    still verifies against them.
    """
    safe_name = _safe_name(video_name)
    stem = os.path.splitext(safe_name)[0]
    try:
        from app.services.genblaze_service import genblaze
        from app.services.storage_service import storage

        manifest: Optional[Dict[str, Any]] = None
        manifest_url: Optional[str] = None

        if storage.available:
            manifest_key = storage.key_for(stem, "manifest.json")
            manifest = storage.read_json(manifest_key)
            if manifest:
                manifest_url = storage.presign(manifest_key)

        if manifest is None:
            # Nothing in the bucket — try reading it back out of the MP4 itself.
            local_video = settings.STATIC_DIR / "videos" / safe_name
            if local_video.exists():
                manifest = genblaze.extract_manifest(local_video)

        if not manifest:
            return ProvenanceResponse(name=safe_name, available=False)

        run = manifest.get("run") or {}
        steps = run.get("steps") or []
        source_meta = (steps[0].get("metadata") if steps else {}) or {}
        assets: List[dict] = []
        for step in steps:
            for asset in step.get("assets") or []:
                assets.append({
                    "asset_id": asset.get("asset_id"),
                    "role": (asset.get("metadata") or {}).get("role"),
                    "filename": (asset.get("metadata") or {}).get("filename"),
                    "media_type": asset.get("media_type"),
                    "sha256": asset.get("sha256"),
                    "size_bytes": asset.get("size_bytes"),
                })

        verification = genblaze.verify_manifest(manifest)
        return ProvenanceResponse(
            name=safe_name,
            available=True,
            verified=bool(verification.get("verified")),
            canonical_hash=manifest.get("canonical_hash"),
            run_id=run.get("run_id"),
            tenant_id=run.get("tenant_id"),
            created_at=run.get("created_at"),
            stages=source_meta.get("stages") or [],
            assets=assets,
            manifest=manifest,
            verification=verification,
            manifest_url=manifest_url,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to load provenance: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to load provenance: {str(e)}")


@router.delete("/{video_name}")
async def delete_video(video_name: str):
    """
    Delete a video and every artefact belonging to it, from B2 and local disk.
    """
    safe_name = _safe_name(video_name)
    try:
        from app.services.storage_service import storage

        stem = os.path.splitext(safe_name)[0]
        deleted = False

        if storage.available and storage.get_item(stem) is not None:
            deleted = storage.delete_video(stem)

        video_dir = settings.STATIC_DIR / "videos"
        video_path = video_dir / safe_name
        if video_path.exists():
            video_path.unlink()
            deleted = True
            thumb_path = video_dir / f"{stem}.jpg"
            if thumb_path.exists():
                thumb_path.unlink()

        if not deleted:
            raise HTTPException(status_code=404, detail="Video not found")

        logger.info(f"Deleted video: {safe_name}")
        return {"success": True, "message": f"Deleted {safe_name}"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete video: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to delete video: {str(e)}")
