"""
Image Sourcing Module - Path-Agnostic Version
Primary source: Pexels stock photo search.
Fallback: Google Gemini image generation (used only when Pexels has no match).
All paths are passed as parameters - no hardcoded paths.
"""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import requests
from google import genai


# Map script aspect ratios to Pexels orientation values
_ORIENTATION_MAP = {
    "1:1": "square",
    "4:3": "landscape",
    "16:9": "landscape",
    "3:4": "portrait",
    "9:16": "portrait",
}


def _orientation_for(aspect_ratio: str) -> str:
    return _ORIENTATION_MAP.get(aspect_ratio, "landscape")


def search_pexels_image(
    query: str,
    api_key: str,
    aspect_ratio: str = "1:1",
) -> Optional[bytes]:
    """
    Search Pexels for a stock photo matching the query and return its bytes.

    Args:
        query: Search query (the scene prompt)
        api_key: Pexels API key
        aspect_ratio: Desired aspect ratio (mapped to Pexels orientation)

    Returns:
        Image bytes if found, otherwise None
    """
    if not api_key:
        return None

    try:
        response = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": api_key},
            params={
                "query": query,
                "per_page": 1,
                "orientation": _orientation_for(aspect_ratio),
            },
            timeout=30,
        )
        response.raise_for_status()
        photos = response.json().get("photos", [])
        if not photos:
            return None

        image_url = photos[0]["src"].get("large2x") or photos[0]["src"].get("large")
        if not image_url:
            return None

        img_response = requests.get(image_url, timeout=30)
        img_response.raise_for_status()
        return img_response.content

    except Exception as e:
        print(f"Pexels lookup failed for '{query}': {e}")
        return None


def generate_gemini_image(
    prompt: str,
    api_key: str,
    model: str = "gemini-2.5-flash-image",
) -> Optional[bytes]:
    """
    Generate an image with Gemini (fallback when Pexels has no match).

    Args:
        prompt: Text prompt for image generation
        api_key: Gemini API key
        model: Gemini image model to use

    Returns:
        Image bytes if successful, otherwise None
    """
    try:
        client = genai.Client(api_key=api_key)

        enhanced_prompt = f"High quality realistic image: {prompt}. Professional photography, detailed, clear focus."

        response = client.models.generate_content(
            model=model,
            contents=enhanced_prompt,
        )

        for candidate in response.candidates or []:
            for part in candidate.content.parts:
                inline_data = getattr(part, "inline_data", None)
                if inline_data is not None and inline_data.data:
                    return inline_data.data
        return None

    except Exception as e:
        print(f"Gemini image generation failed: {e}")
        return None


def get_image_for_prompt(
    prompt: str,
    pexels_api_key: str,
    gemini_api_key: str,
    aspect_ratio: str = "1:1",
    gemini_model: str = "gemini-2.5-flash-image",
) -> Optional[bytes]:
    """
    Get an image for a prompt: try Pexels first, fall back to Gemini.

    Returns:
        Image bytes, or None if both sources fail.
    """
    image_bytes = search_pexels_image(prompt, pexels_api_key, aspect_ratio)
    if image_bytes:
        print(f"Pexels match found for: {prompt}")
        return image_bytes

    print(f"No Pexels match for '{prompt}', falling back to Gemini...")
    return generate_gemini_image(prompt, gemini_api_key, gemini_model)


def save_image(image_bytes: bytes, file_path: Path) -> bool:
    """Save raw image bytes to the specified file path."""
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "wb") as f:
            f.write(image_bytes)
        print(f"Saved: {file_path}")
        return True
    except Exception as e:
        print(f"Error saving image: {e}")
        return False


def main_generate_images(
    script_path: Path,
    images_output_path: Path,
    gemini_api_key: str,
    pexels_api_key: str = "",
    gemini_model: str = "gemini-2.5-flash-image",
    aspect_ratio: str = "1:1",
) -> bool:
    """
    Process the script JSON and source one image per scene.
    Pexels is the primary source; Gemini is the fallback.

    Args:
        script_path: Path to the script JSON file
        images_output_path: Directory where images should be saved
        gemini_api_key: Gemini API key (fallback image generation)
        pexels_api_key: Pexels API key (primary image source)
        gemini_model: Gemini image model used on fallback
        aspect_ratio: Desired image aspect ratio

    Returns:
        True if at least one image was sourced, False otherwise
    """
    script_path = Path(script_path)
    images_output_path = Path(images_output_path)
    images_output_path.mkdir(parents=True, exist_ok=True)

    # Load and parse JSON
    try:
        with open(script_path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except json.JSONDecodeError as e:
        print(f"Error reading JSON file: {e}")
        return False
    except FileNotFoundError:
        print(f"Script file not found: {script_path}")
        return False

    if "visual_script" not in data:
        print("Missing key 'visual_script' in JSON.")
        return False

    scenes = data["visual_script"]

    def _source_one(idx_scene):
        """Fetch + save one scene's image. Returns True on success."""
        idx, scene = idx_scene
        try:
            prompt = scene.get("prompt")
            if not prompt:
                print(f"Scene {idx}: Missing prompt, skipping")
                return False

            timestamp = scene.get("timestamp_start", f"{idx:03d}")
            scene_id = timestamp.replace(":", "-")

            print(f"Sourcing image for prompt: {prompt}")
            image_bytes = get_image_for_prompt(
                prompt,
                pexels_api_key=pexels_api_key,
                gemini_api_key=gemini_api_key,
                aspect_ratio=aspect_ratio,
                gemini_model=gemini_model,
            )
            if not image_bytes:
                print(f"No image obtained for prompt: {prompt}")
                return False

            return save_image(image_bytes, images_output_path / f"scene_{scene_id}.jpg")
        except Exception as e:  # noqa: BLE001 - one scene shouldn't kill the batch
            print(f"Error processing scene {idx}: {e}")
            return False

    # Fetch every scene's image CONCURRENTLY (Pexels is a plain HTTP GET, so a
    # sequential loop with sleeps was the bottleneck). Bounded pool keeps the
    # Gemini image fallback from hammering the API.
    max_workers = min(4, max(1, len(scenes)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        results = list(pool.map(_source_one, enumerate(scenes)))

    success_count = sum(1 for ok in results if ok)
    print(f"Image sourcing completed. {success_count}/{len(scenes)} images obtained.")
    return success_count > 0
