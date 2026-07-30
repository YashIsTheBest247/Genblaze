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
    TRENDS_INTERVAL_HOURS: float = Field(default=6.0, env="TRENDS_INTERVAL_HOURS")
    TRENDS_TOP_N: int = Field(default=3, env="TRENDS_TOP_N")
    TRENDS_VIDEO_DURATION: int = Field(default=60, env="TRENDS_VIDEO_DURATION")
    TRENDS_MAX_AGE_HOURS: float = Field(default=24.0, env="TRENDS_MAX_AGE_HOURS")
    TRENDS_AUTO_PUBLISH: bool = Field(default=False, env="TRENDS_AUTO_PUBLISH")
    TRENDS_RUN_ON_STARTUP: bool = Field(default=False, env="TRENDS_RUN_ON_STARTUP")
    TRENDS_STATE_FILE: Optional[Path] = Field(default=None)

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
