"""
Application Configuration
Loads all settings from environment variables
"""
import os
from pathlib import Path
from typing import List, Optional
from pydantic_settings import BaseSettings
from pydantic import Field
from dotenv import load_dotenv

# Load .env file explicitly
load_dotenv()


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""
    
    # Application
    APP_NAME: str = "Flux Video Generator"
    VERSION: str = "1.0.0"
    ENVIRONMENT: str = Field(default="development", env="ENVIRONMENT")
    DEBUG: bool = Field(default=True, env="DEBUG")
    HOST: str = Field(default="0.0.0.0", env="HOST")
    PORT: int = Field(default=8000, env="PORT")
    
    # CORS (includes the Vite dev server origins used by the frontend)
    CORS_ORIGINS: str = Field(
        default="http://localhost:8000,http://127.0.0.1:8000,http://localhost:5173,http://127.0.0.1:5173",
        env="CORS_ORIGINS"
    )
    
    @property
    def cors_origins_list(self) -> List[str]:
        """Parse CORS_ORIGINS into a list. Trailing slashes are stripped because
        browsers send the Origin header without one (an exact match would fail)."""
        raw = self.CORS_ORIGINS if isinstance(self.CORS_ORIGINS, str) else ",".join(self.CORS_ORIGINS)
        return [origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip()]
    
    # API Keys
    # Not hard-required at import time: Flux can now run script/image generation
    # through other Genblaze providers, and a missing key should surface as a
    # degraded /health readiness report rather than a container that won't boot.
    GEMINI_API_KEY: str = Field(default="", env="GEMINI_API_KEY")

    # Additional Gemini keys, comma-separated. The free tier caps generate_content
    # at 20 requests PER DAY per project, and one render spends two of them, so a
    # single key runs dry after ~10 videos. Each extra key here is a separate
    # project quota the script generator rotates to when the current one reports a
    # per-day exhaustion.
    GEMINI_API_KEYS: str = Field(default="", env="GEMINI_API_KEYS")

    # Pexels API key - primary source for scene images (optional; falls back to Gemini)
    PEXELS_API_KEY: str = Field(default="", env="PEXELS_API_KEY")

    # Unsplash access key - second stock library, searched alongside Pexels.
    # Optional: without it the pipeline is Pexels-only, exactly as before. With
    # it, both libraries are queried concurrently and the result that best
    # matches the scene prompt wins, which is the main defence against the
    # loosely-related filler a single library returns when it has no real match.
    UNSPLASH_ACCESS_KEY: str = Field(default="", env="UNSPLASH_ACCESS_KEY")

    # Gemini text generation model
    GEMINI_TEXT_MODEL: str = Field(default="gemini-2.5-flash", env="GEMINI_TEXT_MODEL")

    # Script provider: "gemini" (free tier, rate-limited) or "ollama" (local, free,
    # unlimited — requires Ollama running with the model pulled).
    SCRIPT_PROVIDER: str = Field(default="gemini", env="SCRIPT_PROVIDER")
    OLLAMA_MODEL: str = Field(default="llama3.2", env="OLLAMA_MODEL")
    OLLAMA_BASE_URL: str = Field(default="http://localhost:11434", env="OLLAMA_BASE_URL")

    @property
    def gemini_api_keys_list(self) -> List[str]:
        """
        Every Gemini key available to the script generator, primary first.

        Deduplicated because pasting the primary key into GEMINI_API_KEYS as well
        is an easy mistake, and a duplicate would make the pool believe it has
        spare quota it does not have.
        """
        raw = [self.GEMINI_API_KEY] + self.GEMINI_API_KEYS.split(",")
        keys: List[str] = []
        for key in raw:
            key = key.strip()
            if key and key not in keys:
                keys.append(key)
        return keys

    # Paths
    BASE_DIR: Path = Path(__file__).resolve().parent.parent.parent
    STATIC_DIR: Optional[Path] = Field(default=None)
    RESOURCE_DIR: Optional[Path] = Field(default=None)
    
    # Resource subdirectories
    SCRIPT_DIR: Optional[Path] = Field(default=None)
    IMAGES_DIR: Optional[Path] = Field(default=None)
    AUDIO_DIR: Optional[Path] = Field(default=None)
    VIDEO_OUTPUT_DIR: Optional[Path] = Field(default=None)
    SUBTITLE_OUTPUT_DIR: Optional[Path] = Field(default=None)
    FONT_PATH: Optional[Path] = Field(default=None)
    INTRO_IMAGE_PATH: Optional[Path] = Field(default=None)

    # Video generation settings
    DEFAULT_VIDEO_DURATION: int = Field(default=60, env="DEFAULT_VIDEO_DURATION")
    # 20 rather than 24: encode time is linear in frame count, and the content is
    # still photographs with crossfades — there is no motion for the extra 4 frames
    # a second to resolve. Raise it if real video footage is ever mixed in.
    DEFAULT_VIDEO_FPS: int = Field(default=20, env="DEFAULT_VIDEO_FPS")
    DEFAULT_CHUNK_SIZE: int = Field(default=10, env="DEFAULT_CHUNK_SIZE")
    # Intro/outro lengths (seconds). Kept short so the total video tracks the
    # user-selected duration instead of being dominated by bookends.
    INTRO_SECONDS: float = Field(default=2.0, env="INTRO_SECONDS")
    OUTRO_SECONDS: float = Field(default=2.0, env="OUTRO_SECONDS")
    # Narration speaking rate (words/second) used to target script length.
    WORDS_PER_SECOND: float = Field(default=2.5, env="WORDS_PER_SECOND")
    
    # Image settings - Pexels primary, Gemini fallback
    IMAGE_GEN_MODEL: str = Field(default="gemini-2.5-flash-image", env="IMAGE_GEN_MODEL")
    # Shape of the scene images to search for / generate. Defaults to the vertical
    # frame the video is actually assembled in.
    IMAGE_ASPECT_RATIO: str = Field(default="9:16", env="IMAGE_ASPECT_RATIO")

    # Use short stock VIDEO clips for scenes instead of still photographs, where
    # a clip is genuinely about the story. Motion holds attention far better on
    # a vertical feed. Only used when the clip's own slug matches the scene
    # prompt, so an off-topic clip never displaces a correct photo.
    SCENE_VIDEO_CLIPS: bool = Field(default=True, env="SCENE_VIDEO_CLIPS")
    # Smallest clip rendition to download. Pexels masters are 4K; anything above
    # the output frame is decoded and thrown away, costing render time and the
    # peak memory that OOM-killed the container once already.
    SCENE_VIDEO_MIN_HEIGHT: int = Field(default=854, env="SCENE_VIDEO_MIN_HEIGHT")
    # How many scenes may use video. Clips go to the OPENING scenes, which is
    # where they earn their keep — a viewer decides in the first second or two.
    # Lowered automatically on hosts that cannot hold this many; see
    # scene_video_max_clips_effective.
    SCENE_VIDEO_MAX_CLIPS: int = Field(default=4, env="SCENE_VIDEO_MAX_CLIPS")

    @property
    def container_memory_limit_mb(self) -> Optional[int]:
        """
        The container's memory ceiling in MB, or None when unlimited/unknown.

        Read from the cgroup rather than from total system memory: a container is
        capped well below the host's RAM, and it is the cap that the OOM killer
        enforces. Both cgroup versions are checked because hosts differ.
        """
        for path in ("/sys/fs/cgroup/memory.max",                    # cgroup v2
                     "/sys/fs/cgroup/memory/memory.limit_in_bytes"):  # cgroup v1
            try:
                raw = Path(path).read_text().strip()
            except OSError:
                continue
            if raw == "max":
                return None
            try:
                value = int(raw)
            except ValueError:
                continue
            # v1 reports a sentinel near 2^63 when there is no limit.
            if value <= 0 or value > (1 << 60):
                return None
            return value // (1024 * 1024)
        return None

    @property
    def scene_video_max_clips_effective(self) -> int:
        """
        How many scenes may use video, given what this host can actually hold.

        Each open scene clip keeps an ffmpeg decoder alive for the whole write —
        roughly 125 MB measured — on top of a ~380 MB still-image baseline. So the
        ceiling is arithmetic, not preference: a 1 GB container (which reports
        about 953 MB of cgroup limit) has room for three, not the five that would
        look best.

        Configure SCENE_VIDEO_MAX_CLIPS for the number you want; this only ever
        lowers it to what fits.
        """
        wanted = max(0, self.SCENE_VIDEO_MAX_CLIPS)
        limit = self.container_memory_limit_mb
        if limit is None or not self.SCENE_VIDEO_MEMORY_GATE:
            return wanted
        # Leave 20% headroom over the projected peak: the render is not the only
        # thing in the container, and being killed costs the whole video.
        affordable = int((limit * 0.8 - self.SCENE_VIDEO_BASELINE_MB) // self.SCENE_VIDEO_CLIP_COST_MB)
        return max(0, min(wanted, affordable))

    # Calibrated against real 60-second renders rather than estimated: 383 MB on
    # stills only, 654 MB with two clips, 793 MB with three. That is ~137 MB per
    # clip on a ~385 MB baseline, consistent across both measurements.
    SCENE_VIDEO_BASELINE_MB: int = Field(default=390, env="SCENE_VIDEO_BASELINE_MB")
    SCENE_VIDEO_CLIP_COST_MB: int = Field(default=140, env="SCENE_VIDEO_CLIP_COST_MB")
    # Set false to use SCENE_VIDEO_MAX_CLIPS verbatim and skip the memory check.
    # The gate is a floor under a real failure mode, not a preference — a render
    # that gets OOM-killed produces no video at all — but the call is yours to
    # make if you would rather have the extra clip and accept the risk.
    SCENE_VIDEO_MEMORY_GATE: bool = Field(default=True, env="SCENE_VIDEO_MEMORY_GATE")

    @property
    def scene_video_clips_effective(self) -> bool:
        """
        Whether scene video clips are actually used on this host.

        Each open scene clip holds an ffmpeg decoder for the whole write. Measured
        peaks: 383 MB for a 30-second render on stills, 504 MB with one clip,
        654 MB for 60 seconds with two. On a 512 MB container that is the
        difference between a finished video and an OOM kill — and a killed render
        produces nothing at all, which is strictly worse than a still image.

        So the setting is honoured wherever there is room for it, and overridden
        only where the container demonstrably cannot hold it.
        """
        if not self.SCENE_VIDEO_CLIPS:
            return False
        limit = self.container_memory_limit_mb
        return limit is None or limit >= self.SCENE_VIDEO_MIN_MEMORY_MB

    # Memory below which scene clips are turned off automatically.
    #
    # 768 rather than 1024: the worst case actually measured — a 60-second render,
    # seven scenes, the full two-clip cap — peaks at 654 MB, and the cap keeps it
    # there however long the video gets. A 1 GB container reports about 953 MB of
    # usable cgroup limit, so a 1024 threshold disabled clips on exactly the boxes
    # that can run them. The earlier OOM was the uncapped path (seven scenes at
    # ~155 MB each, ~1.2 GB), not this one.
    SCENE_VIDEO_MIN_MEMORY_MB: int = Field(default=768, env="SCENE_VIDEO_MIN_MEMORY_MB")

    @property
    def image_aspect_ratio_effective(self) -> str:
        """
        The aspect ratio actually used for scene images.

        Every scene image is cover-cropped to the output frame, so searching in a
        different shape than the video can only ever hurt: it crops away more of
        the photo AND draws from a much smaller, worse-matched stock pool. With
        IMAGE_ASPECT_RATIO=1:1 against a vertical video, "Indian rupee banknotes
        close up" returned Turkish lira and Indian flags — the square pool simply
        had no real match, and Pexels returned its loosest one without saying so.

        So a configured ratio whose orientation contradicts the output frame is
        overridden rather than obeyed. This also protects deployments whose env
        still carries the old value.
        """
        width = int(os.environ.get("FLUX_VIDEO_WIDTH", "480"))
        height = int(os.environ.get("FLUX_VIDEO_HEIGHT", "854"))
        frame = "9:16" if height > width else ("16:9" if width > height else "1:1")

        configured = (self.IMAGE_ASPECT_RATIO or "").strip()
        portrait = {"9:16", "3:4"}
        landscape = {"16:9", "4:3"}
        groups = [portrait, landscape, {"1:1"}]
        same_orientation = any(configured in g and frame in g for g in groups)
        return configured if same_orientation else frame

    # TTS settings
    TTS_LANG_CODE: str = Field(default="b", env="TTS_LANG_CODE")

    # ------------------------------------------------------------------
    # Backblaze B2 — durable object storage for the media library
    # ------------------------------------------------------------------
    # Flux is B2-first: every finished render (mp4 + thumbnail + srt + script +
    # provenance manifest) is uploaded to B2 and served from there. Local disk is
    # scratch space only, which is what makes ephemeral hosts (Render/Railway/
    # Fly/HF Spaces free tiers) viable — the library survives a container restart.
    B2_KEY_ID: str = Field(default="", env="B2_KEY_ID")
    B2_APP_KEY: str = Field(default="", env="B2_APP_KEY")
    B2_BUCKET: str = Field(default="", env="B2_BUCKET")
    # Region slug from the bucket's S3 endpoint (s3.us-west-004... -> us-west-004).
    B2_REGION: str = Field(default="", env="B2_REGION")
    # Key namespace inside the bucket. Genblaze runs land under <prefix>/runs/.
    B2_PREFIX: str = Field(default="flux", env="B2_PREFIX")
    # Lifetime of the presigned GET URLs handed to the browser.
    B2_URL_TTL_SECONDS: int = Field(default=3600, env="B2_URL_TTL_SECONDS")
    # Keep the rendered mp4 on local disk after a successful B2 upload. Off by
    # default (B2-first): the local copy is deleted once B2 has the bytes.
    KEEP_LOCAL_COPY: bool = Field(default=False, env="KEEP_LOCAL_COPY")

    @property
    def b2_configured(self) -> bool:
        """True when enough B2 credentials are present to use object storage."""
        return bool(self.B2_KEY_ID and self.B2_APP_KEY and self.B2_BUCKET)

    # ------------------------------------------------------------------
    # Genblaze — generative media orchestration + provenance
    # ------------------------------------------------------------------
    GENBLAZE_ENABLED: bool = Field(default=True, env="GENBLAZE_ENABLED")
    # Tenant namespace recorded on every Run/Manifest and used in the B2 key path.
    GENBLAZE_TENANT_ID: str = Field(default="flux", env="GENBLAZE_TENANT_ID")
    # Bucket layout: "hierarchical" (runs grouped by tenant/date/run_id) or
    # "content_addressable" (deduplicated by sha256).
    GENBLAZE_KEY_STRATEGY: str = Field(default="hierarchical", env="GENBLAZE_KEY_STRATEGY")
    # Write the provenance manifest into the mp4 itself, so the file carries its
    # own generation record even after it leaves B2.
    GENBLAZE_EMBED_MANIFEST: bool = Field(default=True, env="GENBLAZE_EMBED_MANIFEST")
    # Whether the provenance ingest ALSO pushes every artefact through its object
    # storage sink. Off by default because it is pure duplication: the storage
    # stage already uploads the mp4, thumbnail, captions, script and the manifest
    # itself to B2, so sinking re-uploaded the same megabytes a second time and
    # cost ~15s of every render. The manifest is still built, verified and
    # embedded in the mp4 either way — only the duplicate copies are skipped.
    GENBLAZE_SINK_ASSETS: bool = Field(default=False, env="GENBLAZE_SINK_ASSETS")

    # GMI Cloud — unlocks Genblaze cloud image/audio/video models. Optional:
    # without it Flux falls back to the Google provider + local Kokoro TTS.
    GMI_API_KEY: str = Field(default="", env="GMI_API_KEY")

    # Scene image source.
    #   auto     -> Pexels (primary) then Gemini generation (fallback) — default
    #   genblaze -> Genblaze generation first, still falling back to Pexels/Gemini
    #   pexels   -> Pexels only
    #   gemini   -> Gemini generation only
    # Genblaze is opt-in here on purpose: stock photography is the better and
    # cheaper primary for real news subjects. Genblaze still records the
    # provenance manifest and owns B2 storage on every render regardless.
    IMAGE_PROVIDER: str = Field(default="auto", env="IMAGE_PROVIDER")
    # Genblaze image model. Routing is by id:
    #   gemini-*-image -> GeminiImageProvider (generateContent), needs GEMINI_API_KEY
    #   imagen-*       -> genblaze_google.ImagenProvider (predict); note Google has
    #                     closed every imagen-* model to new API keys
    #   anything else  -> GMI Cloud, needs GMI_API_KEY
    GENBLAZE_IMAGE_MODEL: str = Field(
        default="gemini-2.5-flash-image", env="GENBLAZE_IMAGE_MODEL"
    )

    # Narration source.
    #   auto     -> Genblaze (if GMI key) -> Edge TTS -> Kokoro   [default]
    #   genblaze -> Genblaze cloud TTS (needs GMI_API_KEY)
    #   edge     -> Edge TTS: free, no key, no local model
    #   kokoro   -> local Kokoro (needs the optional torch deps)
    #
    # Edge is the default because Kokoro loads torch and pulls a ~350 MB model on
    # first use. On a container with ephemeral disk that happens mid-render and
    # the memory spike OOM-kills the process — it did, reproducibly, in
    # production. Edge is a network call with negligible memory.
    TTS_PROVIDER: str = Field(default="auto", env="TTS_PROVIDER")
    # Edge TTS voices. `edge-tts --list-voices` shows all 47 English options.
    EDGE_TTS_VOICE_MALE: str = Field(default="en-GB-RyanNeural", env="EDGE_TTS_VOICE_MALE")
    EDGE_TTS_VOICE_FEMALE: str = Field(default="en-GB-SoniaNeural", env="EDGE_TTS_VOICE_FEMALE")
    GENBLAZE_TTS_MODEL: str = Field(
        default="minimax-tts-speech-2.6-turbo", env="GENBLAZE_TTS_MODEL"
    )
    GENBLAZE_TTS_VOICE: str = Field(default="", env="GENBLAZE_TTS_VOICE")

    @property
    def gmi_configured(self) -> bool:
        return bool(self.GMI_API_KEY)

    # YouTube auto-publishing
    # When true, EVERY generated video is published (not just ones where the UI
    # toggle/publish_to_youtube flag is set). The UI toggle can still enable
    # publishing per-render even when this is false.
    YOUTUBE_AUTO_UPLOAD: bool = Field(default=False, env="YOUTUBE_AUTO_UPLOAD")
    YOUTUBE_PRIVACY_STATUS: str = Field(default="private", env="YOUTUBE_PRIVACY_STATUS")
    YOUTUBE_CATEGORY_ID: str = Field(default="27", env="YOUTUBE_CATEGORY_ID")  # 27 = Education
    YOUTUBE_DEFAULT_TAGS: str = Field(default="education,flux,ai", env="YOUTUBE_DEFAULT_TAGS")
    SECRETS_DIR: Optional[Path] = Field(default=None)
    YOUTUBE_CLIENT_SECRET_FILE: Optional[Path] = Field(default=None)
    YOUTUBE_TOKEN_FILE: Optional[Path] = Field(default=None)
    # Deployment fallback: paste the contents of youtube_token.json here when the
    # filesystem is ephemeral/read-only (e.g. Render). Takes priority over the file.
    YOUTUBE_TOKEN_JSON: str = Field(default="", env="YOUTUBE_TOKEN_JSON")

    @property
    def youtube_tags_list(self) -> List[str]:
        """Parse YOUTUBE_DEFAULT_TAGS into a list"""
        return [tag.strip() for tag in self.YOUTUBE_DEFAULT_TAGS.split(",") if tag.strip()]

    # Trending pipeline (Economic Times RSS -> rank -> short-form video)
    # Disabled by default: enabling starts a background scheduler that consumes
    # the Gemini quota and (optionally) uploads to YouTube unattended.
    TRENDS_ENABLED: bool = Field(default=False, env="TRENDS_ENABLED")
    TRENDS_FEED_URLS: str = Field(
        default=(
            "https://economictimes.indiatimes.com/rssfeedstopstories.cms,"
            "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms,"
            "https://economictimes.indiatimes.com/industry/rssfeeds/13352306.cms,"
            "https://economictimes.indiatimes.com/news/economy/rssfeeds/1373380680.cms,"
            "https://economictimes.indiatimes.com/tech/rssfeeds/13357270.cms"
        ),
        env="TRENDS_FEED_URLS",
    )
    # Fixed times of day to publish, rather than "every N hours from whenever the
    # process happened to start". An interval anchored to boot never came due on
    # a host that redeploys, and even when it did the slots drifted daily.
    # Comma-separated 24-hour local times, interpreted in TRENDS_TIMEZONE.
    TRENDS_SCHEDULE_HOURS: str = Field(default="8,14,20", env="TRENDS_SCHEDULE_HOURS")
    TRENDS_TIMEZONE: str = Field(default="Asia/Kolkata", env="TRENDS_TIMEZONE")
    # Kept for the status API and as the catch-up window: a run is considered
    # missed if the last one predates the most recent slot that has passed.
    TRENDS_INTERVAL_HOURS: float = Field(default=6.0, env="TRENDS_INTERVAL_HOURS")
    # Videos per scheduled slot. 1 x 3 slots = 3 videos a day.
    TRENDS_TOP_N: int = Field(default=1, env="TRENDS_TOP_N")
    TRENDS_VIDEO_DURATION: int = Field(default=60, env="TRENDS_VIDEO_DURATION")
    TRENDS_MAX_AGE_HOURS: float = Field(default=24.0, env="TRENDS_MAX_AGE_HOURS")
    TRENDS_AUTO_PUBLISH: bool = Field(default=False, env="TRENDS_AUTO_PUBLISH")
    TRENDS_RUN_ON_STARTUP: bool = Field(default=False, env="TRENDS_RUN_ON_STARTUP")
    TRENDS_STATE_FILE: Optional[Path] = Field(default=None)

    @property
    def trends_schedule_hours(self) -> List[int]:
        """Scheduled publish hours, parsed and ordered; bad entries are dropped."""
        hours: List[int] = []
        for part in (self.TRENDS_SCHEDULE_HOURS or "").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                hour = int(part)
            except ValueError:
                continue
            if 0 <= hour <= 23 and hour not in hours:
                hours.append(hour)
        return sorted(hours) or [8, 14, 20]

    @property
    def trends_feed_urls_list(self) -> List[str]:
        """Parse TRENDS_FEED_URLS into a list"""
        return [url.strip() for url in self.TRENDS_FEED_URLS.split(",") if url.strip()]
    
    # Logging
    LOG_FILE: str = Field(default="app.log", env="LOG_FILE")
    LOG_LEVEL: str = Field(default="INFO", env="LOG_LEVEL")
    
    # Background task settings
    TASK_TIMEOUT: int = Field(default=600, env="TASK_TIMEOUT")  # 10 minutes
    IMAGE_GEN_DELAY: float = Field(default=2.0, env="IMAGE_GEN_DELAY")  # Delay between image gen calls
    
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
        "extra": "ignore"
    }
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        
        # Initialize paths after base initialization
        if self.STATIC_DIR is None:
            self.STATIC_DIR = self.BASE_DIR / "static"

        if self.RESOURCE_DIR is None:
            self.RESOURCE_DIR = self.BASE_DIR / "resources"

        # Secrets (YouTube OAuth credentials)
        if self.SECRETS_DIR is None:
            self.SECRETS_DIR = self.BASE_DIR / "secrets"

        if self.YOUTUBE_CLIENT_SECRET_FILE is None:
            self.YOUTUBE_CLIENT_SECRET_FILE = self.SECRETS_DIR / "youtube_client_secret.json"

        if self.YOUTUBE_TOKEN_FILE is None:
            self.YOUTUBE_TOKEN_FILE = self.SECRETS_DIR / "youtube_token.json"

        # Trending pipeline state (processed article links, for dedup)
        if self.TRENDS_STATE_FILE is None:
            self.TRENDS_STATE_FILE = self.RESOURCE_DIR / "trends_state.json"
        
        # Resource subdirectories
        if self.SCRIPT_DIR is None:
            self.SCRIPT_DIR = self.RESOURCE_DIR / "scripts"
        
        if self.IMAGES_DIR is None:
            self.IMAGES_DIR = self.RESOURCE_DIR / "images"
        
        if self.AUDIO_DIR is None:
            self.AUDIO_DIR = self.RESOURCE_DIR / "audio"
        
        if self.VIDEO_OUTPUT_DIR is None:
            self.VIDEO_OUTPUT_DIR = self.RESOURCE_DIR / "video"
        
        if self.SUBTITLE_OUTPUT_DIR is None:
            self.SUBTITLE_OUTPUT_DIR = self.RESOURCE_DIR / "subtitles"
        
        if self.FONT_PATH is None:
            self.FONT_PATH = self.RESOURCE_DIR / "font" / "font.ttf"
        
        if self.INTRO_IMAGE_PATH is None:
            self.INTRO_IMAGE_PATH = self.RESOURCE_DIR / "intro" / "intro.jpg"
    
    def ensure_directories(self):
        """Ensure all required directories exist"""
        directories = [
            self.STATIC_DIR / "videos",
            self.RESOURCE_DIR,
            self.SCRIPT_DIR,
            self.IMAGES_DIR,
            self.AUDIO_DIR,
            self.VIDEO_OUTPUT_DIR,
            self.SUBTITLE_OUTPUT_DIR,
            self.RESOURCE_DIR / "font",
            self.RESOURCE_DIR / "intro"
        ]
        
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)


# Create global settings instance
settings = Settings()
