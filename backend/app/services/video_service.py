"""
Video Generation Service
Orchestrates the entire video generation pipeline
All paths are dynamically resolved from configuration
"""
import logging
import json
import os
import time
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from fastapi import BackgroundTasks

from app.schemas.video import VideoGenerationRequest, VideoGenerationResponse
from app.core.config import settings
from imagegen.generate_script import VideoScriptGenerator
# NOTE: the image/TTS/assembly pipeline (which pulls in torch, kokoro, moviepy,
# opencv) is imported lazily inside _generate_video_task — NOT at module import.
# This keeps the web process light at boot so the API/health/trends endpoints
# survive on small (e.g. 512 MB free-tier) instances; the heavy deps are only
# loaded when an actual render runs in the background.

logger = logging.getLogger(__name__)

# Renders share working directories (resources/images, resources/audio,
# resources/scripts/script.json) and clean them at the start of each run, so two
# renders MUST NOT run concurrently or they corrupt each other. This lock
# serializes every render (manual + trending) to one at a time.
_render_lock = threading.Lock()

# Live render status so the UI can show which pipeline stage is actually running.
# Stage keys match the frontend's pipelineSteps: fetch, trend, script, image,
# voice, subtitles, assembly, publish.
render_status = {
    "active": False,
    "stage": None,
    "topic": None,
    "video_filename": None,
    "error": None,
    "updated_at": None,
}

# Genblaze cloud image generation is attempted first when configured, but a key
# that lacks entitlement (e.g. Imagen on a free-tier Gemini key) would fail once
# per scene, per render, forever. One total failure trips this breaker and the
# process falls straight through to Pexels/Gemini for the rest of its life.
_genblaze_images_disabled = False


def set_stage(stage: Optional[str], **extra) -> None:
    """Record the current pipeline stage (safe to call from any thread)."""
    render_status["stage"] = stage
    render_status["updated_at"] = time.time()
    for key, value in extra.items():
        render_status[key] = value


class VideoGenerationService:
    """Service for generating videos"""
    
    def __init__(self):
        self.script_generator = VideoScriptGenerator(
            api_key=settings.GEMINI_API_KEY,
            api_keys=settings.gemini_api_keys_list,
            model=settings.GEMINI_TEXT_MODEL,
            provider=settings.SCRIPT_PROVIDER,
            ollama_model=settings.OLLAMA_MODEL,
            ollama_base_url=settings.OLLAMA_BASE_URL,
        )
        # Per-render bookkeeping, read back when the provenance record is built.
        # Safe as instance state because _render_lock serializes renders.
        self._last_image_run_id: Optional[str] = None
        self._last_audio_run_id: Optional[str] = None
        self._last_b2_keys: Dict[str, Optional[str]] = {}

    # ------------------------------------------------------------------
    # Genblaze generation adapters
    #
    # Passed down into the image/TTS modules so those stay free of app-level
    # imports. Both are best-effort: returning an empty list makes the caller
    # fall back to its own source.
    # ------------------------------------------------------------------

    def _genblaze_images(
        self,
        prompts: List[str],
        targets: List[Path],
        aspect_ratio: str,
    ) -> Sequence[Optional[Path]]:
        global _genblaze_images_disabled
        if _genblaze_images_disabled:
            return []

        from app.services.genblaze_service import genblaze

        result = genblaze.generate_images(prompts, targets, aspect_ratio=aspect_ratio)
        self._last_image_run_id = result.run_id

        if not result.any_succeeded and (result.error or prompts):
            # Nothing came back at all — almost always a missing entitlement on
            # the configured key. Stop paying the latency on every later render.
            _genblaze_images_disabled = True
            logger.info(
                "Genblaze image generation produced nothing (%s); "
                "using Pexels/Gemini for the rest of this process.",
                result.error or "no assets",
            )
        return result.paths

    def _genblaze_narration(
        self,
        texts: List[str],
        targets: List[Path],
    ) -> Sequence[Optional[Path]]:
        from app.services.genblaze_service import genblaze

        result = genblaze.synthesize_speech(texts, targets)
        self._last_audio_run_id = result.run_id
        return result.paths


    def _clean_directory(self, folder_path: Path):
        """Clean all files in a directory"""
        if not folder_path.exists():
            return
        
        for item in folder_path.iterdir():
            try:
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
            except Exception as e:
                logger.warning(f"Failed to delete {item}: {e}")
    
    def _generate_video_filename(self, topic: str) -> str:
        """Generate a clean filename from topic"""
        clean_topic = re.sub(r"[^A-Za-z0-9]", "_", topic)[:30]
        timestamp = int(time.time())
        return f"{clean_topic}_{timestamp}.mp4"
    
    async def generate_video_async(
        self,
        request: VideoGenerationRequest,
        background_tasks: BackgroundTasks
    ) -> VideoGenerationResponse:
        """
        Generate video asynchronously
        Returns immediately with success status, actual generation happens in background
        """
        try:
            # Generate video filename
            video_filename = self._generate_video_filename(request.topic)
            
            # Add background task
            background_tasks.add_task(
                self._generate_video_task,
                request,
                video_filename
            )
            
            return VideoGenerationResponse(
                success=True,
                message="Video generation started. This may take several minutes.",
                video_path=f"/static/videos/{video_filename}",
                video_filename=video_filename
            )
            
        except Exception as e:
            logger.error(f"Failed to start video generation: {str(e)}", exc_info=True)
            return VideoGenerationResponse(
                success=False,
                message="Failed to start video generation",
                error=str(e)
            )
    
    def _generate_video_task(self, request: VideoGenerationRequest, video_filename: str):
        """
        Background task entry point. Serializes renders so concurrent requests
        never race on the shared working directories — one render at a time.
        """
        if _render_lock.locked():
            logger.info("A render is already in progress; queuing this one...")
        with _render_lock:
            self._run_pipeline(request, video_filename)

    def _run_pipeline(self, request: VideoGenerationRequest, video_filename: str):
        """
        The main pipeline that orchestrates all steps.
        All paths are dynamically resolved from configuration.
        """
        try:
            logger.info(f"Starting video generation for: {request.topic}")
            render_status.update(
                {"active": True, "error": None, "topic": request.topic,
                 "video_filename": video_filename}
            )
            # Per-stage record of what produced this render. It becomes the
            # source metadata of the Genblaze provenance manifest.
            stages: List[Dict[str, Any]] = []
            self._last_image_run_id = None
            self._last_audio_run_id = None
            self._last_b2_keys = {}
            set_stage("script")

            # Lazy imports: load the heavy pipeline only when actually rendering.
            from imagegen.gen_img import main_generate_images
            from tts.generate_audio_refactored import main_generate_audio
            from assembly.scripts.assembly_video_refactored import (
                create_video,
                create_complete_srt,
            )

            # Cap CPU threads to keep peak memory down on small instances.
            try:
                import torch
                torch.set_num_threads(1)
            except Exception:  # noqa: BLE001 - torch optional / best-effort
                pass

            # Step 1: Clean working directories
            logger.info("Cleaning working directories...")
            self._clean_directory(settings.IMAGES_DIR)
            self._clean_directory(settings.AUDIO_DIR)
            
            # Step 2: Generate script.
            # Target the narration to fill (selected duration - intro - outro) so
            # the FINAL video length tracks the user's chosen duration.
            overhead = settings.INTRO_SECONDS + settings.OUTRO_SECONDS
            content_duration = max(5, int(round(request.duration - overhead)))
            logger.info(
                f"Generating script (target {content_duration}s narration "
                f"for a {request.duration}s video)..."
            )
            script = self.script_generator.generate_script(
                topic=request.topic,
                duration=content_duration,
                key_points=request.key_points if request.key_points else None,
                words_per_second=settings.WORDS_PER_SECOND,
            )
            
            # Save script
            script_path = settings.SCRIPT_DIR / "script.json"
            script_path.parent.mkdir(parents=True, exist_ok=True)
            self.script_generator.save_script(script, str(script_path))
            logger.info(f"Script saved to: {script_path}")
            
            stages.append({
                "stage": "script",
                "provider": settings.SCRIPT_PROVIDER,
                "model": (settings.GEMINI_TEXT_MODEL
                          if settings.SCRIPT_PROVIDER == "gemini" else settings.OLLAMA_MODEL),
                "target_duration_sec": content_duration,
            })

            # Step 3: Source a distinct image per scene.
            # Pexels is the primary source and Gemini generation the fallback;
            # each scene falls back independently. IMAGE_PROVIDER=genblaze puts
            # Genblaze generation ahead of both.
            set_stage("image")
            logger.info("Generating images...")
            image_sources: dict = {}
            main_generate_images(
                script_path=script_path,
                images_output_path=settings.IMAGES_DIR,
                gemini_api_key=settings.GEMINI_API_KEY,
                pexels_api_key=settings.PEXELS_API_KEY,
                unsplash_api_key=settings.UNSPLASH_ACCESS_KEY,
                video_clips=settings.SCENE_VIDEO_CLIPS,
                video_min_height=settings.SCENE_VIDEO_MIN_HEIGHT,
                gemini_model=settings.IMAGE_GEN_MODEL,
                aspect_ratio=settings.image_aspect_ratio_effective,
                image_provider=settings.IMAGE_PROVIDER,
                genblaze_generator=self._genblaze_images,
                source_log=image_sources,
            )
            logger.info("Images generated successfully")
            # Report the sources that ACTUALLY served scenes, not the configured
            # preference — a manifest claiming a model that never ran would be
            # worse than no manifest at all.
            used = sorted(set(image_sources.values()))
            stages.append({
                "stage": "visuals",
                "provider": "+".join(used) if used else None,
                "model": settings.GENBLAZE_IMAGE_MODEL if "genblaze" in used else None,
                "configured_model": settings.GENBLAZE_IMAGE_MODEL,
                "aspect_ratio": settings.image_aspect_ratio_effective,
                "per_scene_source": image_sources,
                "genblaze_run_id": self._last_image_run_id,
            })

            # Step 4: Generate audio (Genblaze cloud TTS, else local Kokoro)
            set_stage("voice")
            logger.info("Generating audio...")
            voice_source: dict = {}
            main_generate_audio(
                script_path=script_path,
                audio_path=settings.AUDIO_DIR,
                lang_code=settings.TTS_LANG_CODE,
                genblaze_generator=self._genblaze_narration,
                source_log=voice_source,
                provider=settings.TTS_PROVIDER,
                voice_male=settings.EDGE_TTS_VOICE_MALE,
                voice_female=settings.EDGE_TTS_VOICE_FEMALE,
            )
            logger.info("Audio generated successfully")
            used_tts = voice_source.get("provider")
            stages.append({
                "stage": "narration",
                "provider": used_tts,
                "model": {
                    "genblaze": settings.GENBLAZE_TTS_MODEL,
                    "edge": voice_source.get("voice"),
                    "kokoro": "kokoro-82m",
                }.get(used_tts),
                "genblaze_run_id": self._last_audio_run_id,
            })

            # Step 5: Generate subtitles
            set_stage("subtitles")
            logger.info("Generating subtitles...")
            clean_topic = re.sub(r"[^A-Za-z0-9]", "_", request.topic)[:30]
            srt_path = settings.SUBTITLE_OUTPUT_DIR / f"{clean_topic}.srt"
            create_complete_srt(
                script_folder=script_path,
                audio_file_folder=settings.AUDIO_DIR,
                outfile_path=srt_path,
                chunk_size=settings.DEFAULT_CHUNK_SIZE,
                intro_offset=settings.INTRO_SECONDS,
            )
            logger.info(f"Subtitles saved to: {srt_path}")
            
            # Step 6: Assemble video
            set_stage("assembly")
            logger.info("Assembling video...")
            temp_video_path = settings.VIDEO_OUTPUT_DIR / video_filename
            
            create_video(
                image_folder=settings.IMAGES_DIR,
                audio_folder=settings.AUDIO_DIR,
                script_path=script_path,
                font_path=settings.FONT_PATH,
                output_file=temp_video_path,
                intro_image_path=settings.INTRO_IMAGE_PATH,
                with_subtitles=True,
                fps=settings.DEFAULT_VIDEO_FPS,
                intro_duration=settings.INTRO_SECONDS,
                outro_duration=settings.OUTRO_SECONDS,
            )
            
            # Step 7: Copy to static directory
            final_video_path = settings.STATIC_DIR / "videos" / video_filename
            final_video_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(temp_video_path, final_video_path)

            # Step 7b: Generate a thumbnail (poster) for the library
            thumbnail_path = self._generate_thumbnail(final_video_path)

            stages.append({
                "stage": "assembly",
                "provider": "moviepy+ffmpeg",
                "model": None,
                "resolution": (
                    f"{os.getenv('FLUX_VIDEO_WIDTH', '480')}x"
                    f"{os.getenv('FLUX_VIDEO_HEIGHT', '854')}"
                ),
                "fps": settings.DEFAULT_VIDEO_FPS,
            })

            logger.info(f"Video generation complete: {video_filename}")

            # Step 8: Provenance — register the render as a Genblaze ingest run,
            # producing a SHA-256-bound manifest, and embed it into the MP4.
            set_stage("provenance")
            manifest = self._record_provenance(
                request=request,
                video_path=final_video_path,
                thumbnail_path=thumbnail_path,
                srt_path=srt_path,
                script_path=script_path,
                stages=stages,
            )

            # Step 9: Durable storage — push every artefact to Backblaze B2.
            set_stage("storage")
            self._publish_to_b2(
                request=request,
                video_path=final_video_path,
                thumbnail_path=thumbnail_path,
                srt_path=srt_path,
                script_path=script_path,
                manifest=manifest,
                stages=stages,
            )

            # Step 10: Auto-publish to YouTube when the per-render flag is set OR
            # the global YOUTUBE_AUTO_UPLOAD setting is enabled.
            if request.publish_to_youtube or settings.YOUTUBE_AUTO_UPLOAD:
                set_stage("publish")
                self._publish_to_youtube(request, final_video_path)

            # Local disk is scratch space in B2-first mode: drop the copy once
            # the bytes are safely in the bucket.
            if not settings.KEEP_LOCAL_COPY and self._last_b2_keys.get("video"):
                for path in (final_video_path, thumbnail_path):
                    try:
                        if path and Path(path).exists():
                            Path(path).unlink()
                    except OSError as e:
                        logger.warning(f"Could not remove local copy {path}: {e}")

            set_stage("done")

        except Exception as e:
            logger.error(f"Video generation task failed: {str(e)}", exc_info=True)
            set_stage("error", error=str(e))
            raise
        finally:
            render_status["active"] = False

    def _generate_thumbnail(self, video_path: Path) -> Optional[Path]:
        """
        Extract a poster frame from the finished video (saved as <stem>.jpg next
        to it). Falls back to the first scene image. Best-effort: never raises.
        """
        thumb_path = video_path.with_suffix(".jpg")
        try:
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            # Grab a frame from the content (just after the ~5s intro).
            subprocess.run(
                [ffmpeg, "-y", "-ss", "6", "-i", str(video_path),
                 "-frames:v", "1", "-q:v", "3", str(thumb_path)],
                capture_output=True, timeout=60,
            )
            if thumb_path.exists() and thumb_path.stat().st_size > 0:
                logger.info(f"Thumbnail saved: {thumb_path.name}")
                return thumb_path
        except Exception as e:  # noqa: BLE001
            logger.warning(f"ffmpeg thumbnail failed ({e}); trying scene image fallback.")

        # Fallback: use the first generated scene image.
        try:
            images = sorted(settings.IMAGES_DIR.glob("*.jpg")) + sorted(settings.IMAGES_DIR.glob("*.png"))
            if images:
                shutil.copy(images[0], thumb_path)
                logger.info(f"Thumbnail (fallback) saved: {thumb_path.name}")
                return thumb_path
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Thumbnail fallback failed: {e}")
        return None

    def _record_provenance(
        self,
        *,
        request: VideoGenerationRequest,
        video_path: Path,
        thumbnail_path: Optional[Path],
        srt_path: Optional[Path],
        script_path: Optional[Path],
        stages: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """
        Build the Genblaze provenance manifest for this render and embed it into
        the MP4. Best-effort: a render is still valid without it.
        """
        try:
            from app.services.genblaze_service import genblaze

            artefacts = [(video_path, "final_video", "video/mp4")]
            if thumbnail_path and Path(thumbnail_path).exists():
                artefacts.append((thumbnail_path, "thumbnail", "image/jpeg"))
            if srt_path and Path(srt_path).exists():
                artefacts.append((srt_path, "captions", "application/x-subrip"))
            if script_path and Path(script_path).exists():
                artefacts.append((script_path, "script", "application/json"))

            manifest = genblaze.record_render(
                topic=request.topic,
                artefacts=artefacts,
                stages=stages,
                extra={
                    "requested_duration_sec": request.duration,
                    "key_points": request.key_points,
                    "style": request.style,
                },
            )
            if manifest and settings.GENBLAZE_EMBED_MANIFEST:
                # Embed BEFORE upload so the bytes in B2 carry the manifest too.
                # This changes the file, so the manifest's own sha256 for the mp4
                # describes the pre-embed bytes by design — that is the hash of
                # the rendered content, which is what provenance should assert.
                genblaze.embed_manifest(video_path, manifest)
            return manifest
        except Exception as e:  # noqa: BLE001
            logger.error(f"Provenance recording failed: {e}", exc_info=True)
            return None

    def _publish_to_b2(
        self,
        *,
        request: VideoGenerationRequest,
        video_path: Path,
        thumbnail_path: Optional[Path],
        srt_path: Optional[Path],
        script_path: Optional[Path],
        manifest: Optional[Dict[str, Any]],
        stages: List[Dict[str, Any]],
    ) -> None:
        """
        Upload the finished render to Backblaze B2.

        This is what makes the library durable: the video, its poster, captions,
        script and provenance manifest all live in the bucket, so an ephemeral
        host can be restarted or redeployed without losing the library.
        """
        try:
            from app.services.storage_service import storage

            if not storage.available:
                if settings.b2_configured:
                    logger.warning("B2 configured but unavailable — keeping video on local disk.")
                else:
                    logger.info("B2 not configured — video stays on local disk only.")
                return

            stem = Path(video_path).stem
            keys = storage.upload_render(
                stem=stem,
                video_path=video_path,
                thumbnail_path=thumbnail_path,
                srt_path=srt_path,
                script_path=script_path,
                manifest=manifest,
                meta={
                    "topic": request.topic,
                    "duration_sec": request.duration,
                    "key_points": request.key_points,
                    "style": request.style,
                    "created_at": time.time(),
                    "stages": stages,
                    "manifest_hash": (manifest or {}).get("canonical_hash"),
                    "run_id": ((manifest or {}).get("run") or {}).get("run_id"),
                    "verified": bool((manifest or {}).get("_verified")),
                },
            )
            self._last_b2_keys = keys
            logger.info("Render published to B2: %s", keys.get("video"))
        except Exception as e:  # noqa: BLE001 - never fail a render on upload
            logger.error(f"B2 publish failed: {e}", exc_info=True)

    def _publish_to_youtube(self, request: VideoGenerationRequest, video_path: Path):
        """
        Upload the finished video to YouTube. Failures here are logged but do not
        fail the whole generation task — the video is already rendered and saved.
        """
        # Imported lazily so the rest of the pipeline runs even if the YouTube
        # client libraries aren't installed.
        from app.services.youtube_service import upload_video, YouTubeServiceError

        try:
            logger.info("Publishing video to YouTube...")
            # #Shorts in the title/description helps YouTube classify the
            # (vertical, <=60s) video as a Short.
            base_title = request.topic.strip()
            title = f"{base_title} #Shorts"[:100]

            description_parts = [f"{base_title}\n"]
            if request.key_points:
                description_parts.append("In this video:")
                description_parts.extend(f"- {point}" for point in request.key_points)
                description_parts.append("")

            # Attribution and AI disclosure on every upload. YouTube requires
            # synthetic content to be flagged, and since each render ships with a
            # verifiable provenance manifest, saying so is an asset rather than a
            # disclaimer. Deliberately no "#viral" — irrelevant tags read as spam
            # and cost more credibility than they win reach on a news channel.
            description_parts.extend([
                "Sourced from Economic Times.",
                "Narration and visuals are AI-generated from reported news. Every "
                "video is published with a verifiable record of how it was made.",
                "",
                "Not investment advice.",
                "",
                "#Shorts #StockMarket #Sensex #Nifty",
            ])
            description = "\n".join(description_parts)

            result = upload_video(
                file_path=video_path,
                title=title,
                description=description,
                tags=settings.youtube_tags_list + ["shorts"],
                # Explicit request wins; otherwise the configured default.
                privacy_status=request.privacy_status or settings.YOUTUBE_PRIVACY_STATUS,
            )
            logger.info(f"Published to YouTube: {result['url']}")
        except YouTubeServiceError as e:
            logger.error(f"YouTube publishing failed: {e}")
        except Exception as e:  # noqa: BLE001
            logger.error(f"Unexpected error while publishing to YouTube: {e}", exc_info=True)
