"""
Genblaze service — generative media orchestration and provenance for Flux.

Two jobs:

1. **Generation.** Scene visuals and narration can be produced through Genblaze
   `Pipeline` steps, so swapping provider/model is a config change rather than a
   code change (Google Imagen today  , GMI Cloud Seedream/FLUX or ElevenLabs the
   moment a `GMI_API_KEY` is present). Both paths are optional — Flux keeps its
   Pexels and local-Kokoro fallbacks.

2. **Provenance.** Every finished render is registered with `Pipeline.ingest`,
   producing a canonical, SHA-256-bound `Manifest` that records what generated
   each artefact. The manifest is written to Backblaze B2 through Genblaze's
   `ObjectStorageSink`, embedded into the MP4 itself, and served back to the UI
   so a viewer can verify the file they are watching.

Nothing here is allowed to break a render: every entry point degrades to a
"provenance unavailable" result rather than raising into the pipeline.
"""
from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

# Aspect ratio -> pixel hint for providers that want explicit dimensions.
_ASPECT_SIZES = {
    "1:1": (1024, 1024),
    "9:16": (768, 1344),
    "16:9": (1344, 768),
    "3:4": (896, 1152),
    "4:3": (1152, 896),
}


@dataclass
class GenerationResult:
    """Outcome of one Genblaze generation call."""

    paths: List[Optional[Path]]
    run_id: Optional[str] = None
    manifest_uri: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    error: Optional[str] = None

    @property
    def any_succeeded(self) -> bool:
        return any(p is not None for p in self.paths)


def sha256_of(path: Path) -> str:
    """Stream a file through SHA-256 (videos are too big to slurp)."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class GenblazeService:
    """Lazily-initialized façade over the Genblaze SDK."""

    def __init__(self) -> None:
        self._sink = None
        self._sink_lock = threading.Lock()
        self._sink_error: Optional[str] = None
        self._import_error: Optional[str] = None

    # -- availability -------------------------------------------------

    @property
    def enabled(self) -> bool:
        if not settings.GENBLAZE_ENABLED:
            return False
        try:
            import genblaze_core  # noqa: F401
        except Exception as e:  # noqa: BLE001
            self._import_error = str(e)
            return False
        return True

    def status(self) -> Dict[str, Any]:
        """Readiness detail for /health and the UI."""
        return {
            "enabled": self.enabled,
            "tenant_id": settings.GENBLAZE_TENANT_ID,
            "sink": "backblaze-b2" if self.sink is not None else None,
            "sink_error": self._sink_error,
            "import_error": self._import_error,
            "image_provider": self._image_provider_name(),
            "image_model": settings.GENBLAZE_IMAGE_MODEL if self._image_provider_name() else None,
            # Human-readable description of the actual visual cascade in effect.
            "visual_flow": self._visual_flow(),
            "tts_provider": "gmicloud" if self._use_genblaze_tts() else "kokoro",
            "tts_model": settings.GENBLAZE_TTS_MODEL if self._use_genblaze_tts() else None,
            "embed_manifest": settings.GENBLAZE_EMBED_MANIFEST,
        }

    @property
    def sink(self):
        """
        Genblaze ObjectStorageSink backed by B2, or None.

        The sink both uploads generated assets and writes the run manifest, so
        Genblaze runs are durably recorded under ``{B2_PREFIX}/runs/``.
        """
        if self._sink is not None:
            return self._sink
        if not self.enabled or not settings.b2_configured or self._sink_error:
            return None

        with self._sink_lock:
            if self._sink is not None:
                return self._sink
            try:
                from genblaze_core import KeyStrategy, ObjectStorageSink

                from app.services.storage_service import storage

                backend = storage.backend
                if backend is None:
                    self._sink_error = "B2 backend unavailable"
                    return None

                strategy = (
                    KeyStrategy.CONTENT_ADDRESSABLE
                    if settings.GENBLAZE_KEY_STRATEGY.lower().startswith("content")
                    else KeyStrategy.HIERARCHICAL
                )
                self._sink = ObjectStorageSink(
                    backend,
                    prefix=settings.B2_PREFIX.strip("/"),
                    key_strategy=strategy,
                )
                logger.info("Genblaze sink ready (B2 %s, %s)",
                            settings.B2_BUCKET, strategy)
            except Exception as e:  # noqa: BLE001
                self._sink_error = str(e)
                logger.error("Genblaze sink unavailable: %s", e)
                return None
        return self._sink

    # -- provider selection -------------------------------------------

    def _image_provider_name(self) -> Optional[str]:
        """
        Which Genblaze provider (if any) should generate scene images.

        Returns None unless IMAGE_PROVIDER is explicitly "genblaze" — the
        default visual flow is Pexels then Gemini, and Genblaze generation is
        opt-in. Routing is then by model id:
          gemini-*-image  -> our GeminiImageProvider (generateContent)
          imagen-*        -> genblaze_google.ImagenProvider (predict)
          anything else   -> GMI Cloud
        """
        if not self.enabled or settings.IMAGE_PROVIDER.lower() != "genblaze":
            return None
        model = settings.GENBLAZE_IMAGE_MODEL.lower()
        if model.startswith("gemini") and "image" in model:
            return "google-gemini-image" if settings.GEMINI_API_KEY else None
        if model.startswith("imagen"):
            return "google-imagen" if settings.GEMINI_API_KEY else None
        return "gmicloud" if settings.gmi_configured else None

    def _visual_flow(self) -> str:
        """
        The image-source cascade currently in effect, for /health and the UI.

        ASCII arrows deliberately: this string reaches Windows consoles through
        logs, where a U+2192 raises UnicodeEncodeError under cp1252.
        """
        choice = settings.IMAGE_PROVIDER.lower()
        if choice == "pexels":
            return "pexels"
        if choice == "gemini":
            return "gemini"
        if choice == "genblaze":
            provider = self._image_provider_name()
            head = f"genblaze:{provider}" if provider else "genblaze:unavailable"
            return f"{head} -> pexels -> gemini"
        return "pexels -> gemini"

    def _build_image_provider(self, output_dir: Path):
        name = self._image_provider_name()
        if name == "google-gemini-image":
            from app.services.genblaze_gemini_image import GeminiImageProvider

            return GeminiImageProvider(
                api_key=settings.GEMINI_API_KEY, output_dir=output_dir
            ), name
        if name == "google-imagen":
            from genblaze_google import ImagenProvider

            return ImagenProvider(api_key=settings.GEMINI_API_KEY, output_dir=output_dir), name
        if name == "gmicloud":
            from genblaze_gmicloud import GMICloudImageProvider

            return GMICloudImageProvider(api_key=settings.GMI_API_KEY), name
        return None, None

    def _use_genblaze_tts(self) -> bool:
        choice = settings.TTS_PROVIDER.lower()
        if choice == "kokoro":
            return False
        if choice == "genblaze":
            return self.enabled and settings.gmi_configured
        # auto: prefer cloud TTS when a GMI key exists (keeps torch off the host)
        return self.enabled and settings.gmi_configured

    # -- asset helpers ------------------------------------------------

    def make_asset(self, path: Path, *, role: str, media_type: Optional[str] = None, **metadata):
        """
        Build a fully-populated Genblaze Asset from a local file.

        sha256/size are computed here rather than left to the sink, so the
        manifest satisfies ``Manifest.verify()`` even when B2 is not configured.
        """
        from genblaze_core import Asset

        path = Path(path)
        media_type = media_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return Asset(
            url=path.resolve().as_uri(),
            media_type=media_type,
            sha256=sha256_of(path),
            size_bytes=path.stat().st_size,
            metadata={"role": role, "filename": path.name, **metadata},
        )

    def _materialize(self, asset, dest: Path) -> Optional[Path]:
        """Copy a generated asset's bytes to `dest` (handles file:// and https://)."""
        url = getattr(asset, "url", None)
        if not url:
            return None
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if url.startswith("file://"):
                from urllib.parse import unquote, urlparse

                src = Path(unquote(urlparse(url).path).lstrip("/"))
                if not src.exists():
                    return None
                dest.write_bytes(src.read_bytes())
            else:
                response = requests.get(url, timeout=120)
                response.raise_for_status()
                dest.write_bytes(response.content)
            return dest if dest.exists() and dest.stat().st_size > 0 else None
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not materialize asset %s: %s", url, e)
            return None

    # -- generation ---------------------------------------------------

    def generate_images(
        self,
        prompts: List[str],
        output_paths: List[Path],
        aspect_ratio: str = "1:1",
    ) -> GenerationResult:
        """
        Generate one image per prompt through a Genblaze Pipeline.

        Returns a GenerationResult whose `paths` line up with `prompts`; entries
        are None where that scene failed, letting the caller fall back per-scene.
        """
        empty = GenerationResult(paths=[None] * len(prompts))
        if not prompts or not self.enabled:
            return empty

        # The provider must write into the system temp directory: the storage
        # sink refuses to read file:// assets from anywhere else, so writing
        # straight into resources/images would make every sink upload fail.
        # Generated images are copied to their real targets afterwards.
        import shutil
        import tempfile

        work_dir = Path(tempfile.mkdtemp(prefix="flux-visuals-"))
        provider, provider_name = self._build_image_provider(work_dir)
        if provider is None:
            shutil.rmtree(work_dir, ignore_errors=True)
            return empty

        width, height = _ASPECT_SIZES.get(aspect_ratio, _ASPECT_SIZES["1:1"])
        model = settings.GENBLAZE_IMAGE_MODEL

        try:
            from genblaze_core import Modality, Pipeline

            pipeline = Pipeline(
                "flux-visuals",
                tenant_id=settings.GENBLAZE_TENANT_ID,
                max_concurrency=4,
                preflight=False,
            )
            for prompt in prompts:
                pipeline = pipeline.step(
                    provider,
                    model=model,
                    prompt=(
                        f"Editorial news photography: {prompt}. "
                        "Realistic, sharp focus, professional lighting, no text overlay."
                    ),
                    modality=Modality.IMAGE,
                    aspect_ratio=aspect_ratio,
                    width=width,
                    height=height,
                )

            # fail_fast/raise_on_failure off: one bad scene must not void the batch.
            result = pipeline.run(
                sink=self.sink,
                fail_fast=False,
                raise_on_failure=False,
                timeout=180,
            )

            # Copy the staged images out to their real scene paths before the
            # staging directory goes away.
            paths: List[Optional[Path]] = []
            for index, step in enumerate(result.run.steps):
                asset = step.assets[0] if getattr(step, "assets", None) else None
                dest = Path(output_paths[index]) if index < len(output_paths) else None
                paths.append(self._materialize(asset, dest) if asset and dest else None)
            paths.extend([None] * (len(prompts) - len(paths)))

            return GenerationResult(
                paths=paths,
                run_id=result.run.run_id,
                manifest_uri=result.manifest.manifest_uri,
                provider=provider_name,
                model=model,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Genblaze image generation failed: %s", e)
            empty.error = str(e)
            return empty
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def synthesize_speech(
        self,
        segments: List[str],
        output_paths: List[Path],
    ) -> GenerationResult:
        """
        Generate narration audio per script segment through Genblaze.

        Used instead of local Kokoro when a GMI key is configured — that keeps
        torch off the deployment host entirely.
        """
        empty = GenerationResult(paths=[None] * len(segments))
        if not segments or not self._use_genblaze_tts():
            return empty

        model = settings.GENBLAZE_TTS_MODEL
        try:
            from genblaze_core import Modality, Pipeline
            from genblaze_gmicloud import GMICloudAudioProvider

            provider = GMICloudAudioProvider(api_key=settings.GMI_API_KEY)
            pipeline = Pipeline(
                "flux-narration",
                tenant_id=settings.GENBLAZE_TENANT_ID,
                max_concurrency=4,
                preflight=False,
            )
            for text in segments:
                params: Dict[str, Any] = {}
                if settings.GENBLAZE_TTS_VOICE:
                    params["voice"] = settings.GENBLAZE_TTS_VOICE
                pipeline = pipeline.step(
                    provider,
                    model=model,
                    prompt=text,
                    modality=Modality.AUDIO,
                    **params,
                )
            result = pipeline.run(
                sink=self.sink,
                fail_fast=False,
                raise_on_failure=False,
                timeout=300,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Genblaze TTS failed: %s", e)
            empty.error = str(e)
            return empty

        paths: List[Optional[Path]] = []
        for index, step in enumerate(result.run.steps):
            asset = step.assets[0] if getattr(step, "assets", None) else None
            dest = Path(output_paths[index]) if index < len(output_paths) else None
            paths.append(self._materialize(asset, dest) if asset and dest else None)
        paths.extend([None] * (len(segments) - len(paths)))

        return GenerationResult(
            paths=paths,
            run_id=result.run.run_id,
            manifest_uri=result.manifest.manifest_uri,
            provider="gmicloud",
            model=model,
        )

    # -- provenance ---------------------------------------------------

    def record_render(
        self,
        *,
        topic: str,
        artefacts: Iterable[Tuple[Path, str, str]],
        stages: List[Dict[str, Any]],
        extra: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Register a completed render as a Genblaze ingest run.

        `artefacts` is an iterable of (path, role, media_type). `stages` is the
        per-stage record of what produced the render (provider, model, source) —
        it becomes the manifest's source metadata, so the manifest documents the
        whole prompt-to-pipeline chain, not just the final file.

        Returns the manifest as a plain dict (ready to store/serve), or None.
        """
        if not self.enabled:
            return None

        # Genblaze refuses to read file:// assets from outside the system temp
        # directory — a deliberate arbitrary-file-read guard, with no override
        # plumbed through the sink. Our artefacts live under static/ and
        # resources/, so stage copies in temp for the duration of the ingest.
        # The bytes are identical, so every SHA-256 in the manifest still
        # describes the real artefact.
        staging: Optional[Path] = None
        try:
            import shutil
            import tempfile

            from genblaze_core import Pipeline

            staging = Path(tempfile.mkdtemp(prefix="flux-provenance-"))
            assets = []
            for path, role, media_type in artefacts:
                path = Path(path)
                if not path.exists() or path.stat().st_size == 0:
                    continue
                staged = staging / path.name
                shutil.copy2(path, staged)
                assets.append(self.make_asset(staged, role=role, media_type=media_type))

            if not assets:
                return None

            source_metadata: Dict[str, Any] = {
                "app": "flux",
                "topic": topic,
                "stages": stages,
                "recorded_at": time.time(),
            }
            if extra:
                source_metadata.update(extra)

            result = Pipeline.ingest(
                assets,
                source="flux-render-pipeline",
                source_metadata=source_metadata,
                sink=self.sink,
                name="flux-render",
                tenant_id=settings.GENBLAZE_TENANT_ID,
            )
            manifest = json.loads(result.manifest.model_dump_json())
            manifest["_verified"] = bool(result.manifest.verify())
            logger.info(
                "Provenance manifest %s recorded (%d assets, hash %s)",
                result.run.run_id, len(assets), manifest.get("canonical_hash", "")[:12],
            )
            return manifest
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to record provenance manifest: %s", e, exc_info=True)
            return None
        finally:
            if staging is not None:
                import shutil as _shutil

                _shutil.rmtree(staging, ignore_errors=True)

    def embed_manifest(self, video_path: Path, manifest: Dict[str, Any]) -> bool:
        """
        Write the manifest into the MP4 container itself.

        The file then carries its own provenance record wherever it travels —
        downloaded, re-uploaded to YouTube, or handed to someone else.
        """
        if not settings.GENBLAZE_EMBED_MANIFEST or not self.enabled or not manifest:
            return False
        try:
            from genblaze_core import Manifest
            from genblaze_core.media import Mp4Handler

            payload = {k: v for k, v in manifest.items() if not k.startswith("_")}
            handler = Mp4Handler()
            handler.embed(Path(video_path), Manifest.model_validate(payload))

            # Read it straight back. embed() can report success on a container it
            # could not actually annotate, and claiming an embedded record that
            # nobody can extract is worse than admitting it didn't happen.
            embedded = handler.extract(Path(video_path))
            if embedded is None or embedded.canonical_hash != manifest.get("canonical_hash"):
                logger.warning(
                    "Manifest embed did not survive read-back for %s; "
                    "the sidecar manifest on B2 remains authoritative.",
                    Path(video_path).name,
                )
                return False

            logger.info("Embedded provenance manifest into %s", Path(video_path).name)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not embed manifest into %s: %s", video_path, e)
            return False

    def verify_manifest(self, manifest: Dict[str, Any]) -> Dict[str, Any]:
        """
        Re-verify a stored manifest: canonical hash + declared asset integrity.

        Powers the "Verified" badge in the library UI.
        """
        if not manifest:
            return {"verified": False, "reason": "no manifest"}
        try:
            from genblaze_core import Manifest

            payload = {k: v for k, v in manifest.items() if not k.startswith("_")}
            parsed = Manifest.model_validate(payload)
            report = parsed.verification_report()
            return {
                "verified": bool(parsed.verify()),
                "hash_valid": bool(parsed.verify_hash()),
                "canonical_hash": parsed.canonical_hash,
                "assets_missing_sha256": list(parsed.output_asset_ids_missing_sha256()),
                "details": json.loads(report.model_dump_json())
                if hasattr(report, "model_dump_json") else str(report),
            }
        except Exception as e:  # noqa: BLE001
            return {"verified": False, "reason": str(e)}

    @staticmethod
    def extract_manifest(video_path: Path) -> Optional[Dict[str, Any]]:
        """Read an embedded manifest back out of an MP4 (CLI parity)."""
        try:
            from genblaze_core.media import Mp4Handler

            return json.loads(Mp4Handler().extract(Path(video_path)).model_dump_json())
        except Exception:  # noqa: BLE001
            return None


# Module-level singleton.
genblaze = GenblazeService()
