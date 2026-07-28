"""
A Genblaze provider for Google's Gemini image models.

**Why this exists.** Genblaze ships `genblaze_google.ImagenProvider`, which calls
the Imagen `:predict` endpoint. Google has since closed every `imagen-*` model to
new Gemini API keys ("no longer available to new users") and moved image
generation to the `gemini-*-image` models on `generateContent`. So on a
present-day Gemini key the stock Google adapter cannot generate anything, and
Genblaze's image step is unreachable without a second vendor's API key.

Genblaze anticipates this: `SyncProvider` is a documented extension point with a
single abstract method. This adapter implements it against `generateContent`, so
Flux drives real Genblaze-orchestrated generation with the one key it already
requires — and the resulting assets flow through the same Pipeline, sink and
provenance manifest as any first-party provider.

Models: `gemini-2.5-flash-image`, `gemini-3.1-flash-image`, `gemini-3-pro-image`.
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

from genblaze_core.exceptions import ProviderError
from genblaze_core.models.asset import Asset
from genblaze_core.models.enums import Modality, ProviderErrorCode
from genblaze_core.models.step import Step
from genblaze_core.providers import (
    DiscoverySupport,
    ModelRegistry,
    ModelSpec,
    ProviderCapabilities,
    RetryPolicy,
    SyncProvider,
)
from genblaze_core.runnable.config import RunnableConfig

logger = logging.getLogger(__name__)

# Any gemini image model id is accepted; liveness is whatever the API says at
# call time, which is the only source of truth Google actually honours here.
_FALLBACK = ModelSpec(model_id="*", modality=Modality.IMAGE)

_MIME_SUFFIX = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}


class GeminiImageProvider(SyncProvider):
    """
    Genblaze adapter for Gemini image generation via `generateContent`.

    Args:
        api_key: Gemini API key. Falls back to the GEMINI_API_KEY env var.
        output_dir: Where generated images are written. **Keep this under the
            system temp directory** — Genblaze's storage layer refuses to read
            `file://` assets from anywhere else, so a sink upload of images
            written elsewhere would be rejected.
        models: Optional custom ModelRegistry.
        retry_policy: Optional retry policy override.
    """

    name = "google-gemini-image"
    # There is no per-model liveness probe worth running: the catalogue endpoint
    # lists models the key cannot actually call. Fail at call time instead.
    discovery_support = DiscoverySupport.NONE

    @classmethod
    def create_registry(cls) -> ModelRegistry:
        return ModelRegistry(fallback=_FALLBACK)

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supported_modalities=[Modality.IMAGE],
            supported_inputs=["text"],
            models=self._models.known(),
            output_formats=["image/png", "image/jpeg", "image/webp"],
        )

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        output_dir: Optional[Path] = None,
        models: Optional[ModelRegistry] = None,
        retry_policy: Optional[RetryPolicy] = None,
    ) -> None:
        super().__init__(models=models, retry_policy=retry_policy)
        self._api_key = api_key or os.getenv("GEMINI_API_KEY")
        self._output_dir = Path(output_dir) if output_dir else None
        self._client: Any = None

    def _get_client(self):
        if self._client is None:
            try:
                from google import genai
            except ImportError as exc:
                raise ProviderError(
                    "google-genai package not installed. Run: pip install google-genai"
                ) from exc
            if not self._api_key:
                raise ProviderError(
                    "No Gemini API key. Pass api_key or set GEMINI_API_KEY.",
                    error_code=ProviderErrorCode.AUTH,
                )
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def generate(self, step: Step, config: RunnableConfig | None = None) -> Step:
        """Generate image(s) for one step and attach them as assets."""
        client = self._get_client()
        try:
            from google.genai import types

            params = step.params or {}
            prompt = step.prompt or params.get("prompt") or ""
            if not prompt:
                raise ProviderError(
                    "Gemini image generation requires a prompt.",
                    error_code=ProviderErrorCode.INVALID_INPUT,
                )

            config_kwargs: dict = {"response_modalities": ["IMAGE"]}
            aspect_ratio = params.get("aspect_ratio")
            if aspect_ratio:
                # Supported on the image models; harmless to omit otherwise.
                config_kwargs["image_config"] = types.ImageConfig(aspect_ratio=aspect_ratio)

            response = client.models.generate_content(
                model=step.model,
                contents=prompt,
                config=types.GenerateContentConfig(**config_kwargs),
            )

            out_dir = self._output_dir or Path(tempfile.gettempdir())
            out_dir.mkdir(parents=True, exist_ok=True)

            index = 0
            for candidate in response.candidates or []:
                content = getattr(candidate, "content", None)
                for part in getattr(content, "parts", None) or []:
                    inline = getattr(part, "inline_data", None)
                    if inline is None or not inline.data:
                        continue
                    mime = getattr(inline, "mime_type", None) or "image/png"
                    suffix = _MIME_SUFFIX.get(mime, ".png")
                    out_path = out_dir / f"{step.step_id}_{index}{suffix}"
                    out_path.write_bytes(inline.data)
                    step.assets.append(
                        Asset(url=out_path.resolve().as_uri(), media_type=mime)
                    )
                    index += 1

            if not step.assets:
                # A safety block returns 200 with no image part rather than an
                # error, so an empty result is a real outcome, not a bug.
                raise ProviderError(
                    "Gemini returned no image — the prompt may have been blocked "
                    "by safety filters.",
                    error_code=ProviderErrorCode.INVALID_INPUT,
                )

            self._apply_registry_pricing(step)
            return step

        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            from genblaze_google._errors import map_google_error

            raise ProviderError(
                f"Gemini image generation failed: {exc}",
                error_code=map_google_error(exc),
            ) from exc
