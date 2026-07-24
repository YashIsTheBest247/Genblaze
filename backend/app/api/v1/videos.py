"""
Video Generation API Endpoints
"""
import logging
import os
from typing import List
from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse

from app.schemas.video import VideoGenerationRequest, VideoGenerationResponse, VideoListResponse
from app.services.video_service import VideoGenerationService
from app.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# Initialize video service
video_service = VideoGenerationService()


@router.post("/generate", response_model=VideoGenerationResponse)
async def generate_video(
    request: VideoGenerationRequest,
    background_tasks: BackgroundTasks
):
    """
    Generate a video based on the provided parameters.
    This endpoint triggers the full video generation pipeline:
    1. Script generation
    2. Image generation
    3. Audio/TTS generation
    4. Subtitle generation
    5. Video assembly
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
    publish, done, error (or null when idle).
    """
    # Imported lazily so this endpoint stays available even before the heavy
    # render dependencies are loaded.
    from app.services.video_service import render_status as status
    return status


@router.get("/list", response_model=List[VideoListResponse])
async def list_videos():
    """
    List generated videos, NEWEST FIRST (so a fresh render appears at the top
    of the library instead of being buried in os.listdir order).
    """
    try:
        video_dir = settings.STATIC_DIR / "videos"

        if not video_dir.exists():
            return []

        entries = []
        for filename in os.listdir(video_dir):
            if filename.lower().endswith(('.mp4', '.webm', '.ogg', '.mov', '.avi')):
                file_path = video_dir / filename
                try:
                    mtime = file_path.stat().st_mtime
                except OSError:
                    mtime = 0
                entries.append((mtime, filename))

        # Most recently created/modified first.
        entries.sort(key=lambda item: item[0], reverse=True)

        videos = []
        for _, filename in entries:
            thumb_name = f"{os.path.splitext(filename)[0]}.jpg"
            thumbnail = (
                f"/static/videos/{thumb_name}"
                if (video_dir / thumb_name).exists()
                else None
            )
            videos.append(VideoListResponse(
                name=filename,
                path=f"/static/videos/{filename}",
                thumbnail=thumbnail,
            ))

        return videos
        
    except Exception as e:
        logger.error(f"Failed to list videos: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list videos: {str(e)}")


@router.get("/download/{video_name}")
async def download_video(video_name: str):
    """
    Download a specific video file
    """
    try:
        video_path = settings.STATIC_DIR / "videos" / video_name
        
        if not video_path.exists():
            raise HTTPException(status_code=404, detail="Video not found")
        
        return FileResponse(
            path=video_path,
            media_type="video/mp4",
            filename=video_name
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to download video: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to download video: {str(e)}")


@router.get("/stream/{video_name}")
async def stream_video(video_name: str):
    """
    Stream a video file with range support
    """
    try:
        video_path = settings.STATIC_DIR / "videos" / video_name
        
        if not video_path.exists():
            raise HTTPException(status_code=404, detail="Video not found")
        
        return FileResponse(
            path=video_path,
            media_type="video/mp4"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to stream video: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to stream video: {str(e)}")


@router.delete("/{video_name}")
async def delete_video(video_name: str):
    """
    Delete a video and its thumbnail from the library.
    """
    try:
        video_dir = settings.STATIC_DIR / "videos"
        # Prevent path traversal: only allow a bare filename inside video_dir.
        safe_name = os.path.basename(video_name)
        if safe_name != video_name or not safe_name:
            raise HTTPException(status_code=400, detail="Invalid video name")

        video_path = video_dir / safe_name
        if not video_path.exists():
            raise HTTPException(status_code=404, detail="Video not found")

        video_path.unlink()

        # Remove the matching thumbnail if present.
        thumb_path = video_dir / f"{os.path.splitext(safe_name)[0]}.jpg"
        if thumb_path.exists():
            thumb_path.unlink()

        logger.info(f"Deleted video: {safe_name}")
        return {"success": True, "message": f"Deleted {safe_name}"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete video: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to delete video: {str(e)}")
