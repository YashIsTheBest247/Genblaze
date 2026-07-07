import json
import re
import time
import requests
from bs4 import BeautifulSoup
from typing import Dict, List, Optional
from google import genai
from google.genai import types

class VideoScriptGenerator:
    def __init__(
        self,
        api_key: str = None,
        model: str = "gemini-2.5-flash",
        provider: str = "gemini",
        ollama_model: str = "llama3.2",
        ollama_base_url: str = "http://localhost:11434",
    ):
        self.provider = (provider or "gemini").lower()
        self.model = model
        self.ollama_model = ollama_model
        self.ollama_base_url = ollama_base_url.rstrip("/")
        # Only create the Gemini client when actually using Gemini.
        self.client = genai.Client(api_key=api_key) if self.provider == "gemini" else None

        self.system_prompt_segmentation = """
        You are a professional video script segmenter.  
        Your task is to take an existing video script draft and break it down into precise, timestamped segments for both audio and visuals, adhering to strict formatting and parameter guidelines.
        Rules for Segmentation:
        
        1. Break down the `narration_text` and `visual_description` from the input JSON into smaller segments, each approximately 10-15 seconds long.
        2. Generate timestamps ("00:00", "00:15", "00:30", etc.) for each segment in both `audio_script` and `visual_script`.
        3. Maintain *strict synchronization*: The `timestamp` values *must* be identical for corresponding audio and visual segments, and the number of segments in audio_script *must be same* as the number of segments in visual_script.
        4. For each visual segment, expand the general `visual_description` into a *simple and realistic* `prompt` suitable for finding images online. The `prompt` must be exactly 5 words long. Include a corresponding `negative_prompt`. 
        5. Ensure the `prompt` describes a realistic, static image that can be easily found on stock image websites or through web scraping. Avoid abstract or overly complex descriptions.
        6. Choose appropriate values for `speaker`, `speed`, `pitch`, and `emotion` for each audio segment.
        7. Remove unnecessary parameters like `style`, `guidance_scale`, `steps`, `seed`, `width`, and `height` since we are not generating images with Stable Diffusion.
        8. Ensure visual continuity: Use consistent and logical descriptions across consecutive visual segments.
        9. Adhere to the specified ranges for numerical parameters (speed, pitch).
        10. Validate JSON structure before output with the example_json given.

        Input JSON Structure (from previous stage):

        {
            "topic": "Topic Name",
            "overall_narrative": "...",
            "key_sections": [
                {
                    "section_title": "...",
                    "narration_text": "...",
                    "visual_description": "..."
                }
            ]
        }
        
        Output JSON Structure (with all required fields):

        {
            "topic": "Topic Name",
            "description": "description of video",
            "audio_script": [{
                "timestamp": "00:00",
                "text": "Narration text",
                "speaker": "default|narrator_male|narrator_female",
                "speed": 0.9-1.1,
                "pitch": 0.9-1.2,
                "emotion": "neutral|serious|dramatic|mysterious|informative"
            }],
            "visual_script": [{
                "timestamp_start": "00:00",
                "timestamp_end": "00:05",
                "prompt": " a realistic image, e.g., 'Wide panoramic football stadium fans cheering'",
                "negative_prompt": "Avoid abstract images, moving objects, or overly complex designs."
            }]
        }
        """

    def _search_web(self, query: str) -> str:
        """
        Fetch search results using BeautifulSoup and Requests.
        """
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            }
            url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
            response = requests.get(url, headers=headers)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            snippets = [div.text for div in soup.find_all("div", class_="BNeawe s3v9rd AP7Wnd")]
            return " ".join(snippets[:5])
        except Exception as e:
            print(f"Error fetching web results: {e}")
            return ""

    def _generate_ollama(self, prompt: str, system_prompt: str, max_retries: int = 3) -> str:
        """Generate JSON via a local Ollama server (free, unlimited)."""
        url = f"{self.ollama_base_url}/api/chat"
        payload = {
            "model": self.ollama_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.7, "num_predict": 8192},
        }
        last_error = None
        for attempt in range(max_retries):
            try:
                resp = requests.post(url, json=payload, timeout=600)
                resp.raise_for_status()
                return resp.json()["message"]["content"]
            except Exception as e:  # noqa: BLE001
                last_error = e
                if attempt < max_retries - 1:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise RuntimeError(
                    f"Ollama call failed: {e}. Is Ollama running at {self.ollama_base_url} "
                    f"with the model pulled?  (run: ollama pull {self.ollama_model})"
                )
        raise RuntimeError(f"Ollama call failed after {max_retries} attempts: {last_error}")

    def _generate_content(self, prompt: str, system_prompt: str, max_retries: int = 4) -> str:
        """
        Generate content using the configured provider. For Gemini, retries transient
        errors (503/429/500/overloaded) with backoff. Returns clean JSON.
        """
        if self.provider == "ollama":
            return self._generate_ollama(prompt, system_prompt)

        transient_markers = ("503", "429", "500", "UNAVAILABLE", "overloaded", "high demand", "RESOURCE_EXHAUSTED")
        last_error = None
        for attempt in range(max_retries):
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        temperature=0.7,
                        max_output_tokens=8192,
                        response_mime_type="application/json",
                    ),
                )
                return response.text
            except Exception as e:  # noqa: BLE001
                last_error = e
                msg = str(e)
                is_transient = any(m in msg for m in transient_markers)
                if is_transient and attempt < max_retries - 1:
                    # Honor the server's suggested delay (e.g. "retry in 36s") for
                    # rate limits; cap it so a render doesn't hang too long.
                    delay_match = re.search(r"retry in ([\d.]+)s", msg)
                    if delay_match:
                        wait = min(float(delay_match.group(1)) + 1, 45)
                    else:
                        wait = 2 * (attempt + 1)
                    print(f"Gemini transient error (attempt {attempt + 1}); retrying in {wait:.0f}s...")
                    time.sleep(wait)
                    continue
                raise RuntimeError(f"Gemini API call failed: {msg}")
        raise RuntimeError(f"Gemini API call failed after {max_retries} attempts: {last_error}")

    def _extract_json(self, raw_text: str) -> Dict:
        """
        Extract JSON from raw text output.
        """
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError:
            try:
                json_match = re.search(r'```json\n(.*?)\n```', raw_text, re.DOTALL)
                if json_match:
                    return json.loads(json_match.group(1))
                json_match = re.search(r'\{.*\}', raw_text, re.DOTALL)
                return json.loads(json_match.group()) if json_match else {}
            except Exception as e:
                raise ValueError(f"JSON extraction failed: {str(e)}")

    def generate_script(
        self,
        topic: str,
        duration: int = 60,
        key_points: Optional[List[str]] = None,
        words_per_second: float = 2.5,
    ) -> Dict:
        """
        Generate a video script based on the given topic, duration, and key points.

        Args:
            topic (str): The topic of the video.
            duration (int): Target narration length in seconds (default: 60).
            key_points (Optional[List[str]]): Optional key points to include.
            words_per_second (float): Speaking rate used to target word count.

        Returns:
            Dict: A structured video script in JSON format.
        """
        # The narration must fit the target duration. At ~words_per_second, that's
        # roughly this many spoken words total — the strongest lever on length.
        target_words = max(12, int(round(duration * words_per_second)))

        # Fetch web context for the topic
        web_context = self._search_web(topic)

        # SINGLE Gemini call (draft + segmentation combined) to halve free-tier
        # quota usage. Produces the final timestamped audio + visual script directly.
        prompt = f"""Create a complete, ready-to-produce short video script about: {topic}.
        Key points: {key_points or 'Comprehensive coverage'}
        Additional context: {web_context}

        STRICT LENGTH: total spoken narration must be about {target_words} words so the
        finished video runs ~{duration} seconds at ~{words_per_second} words/second. Do not exceed it.

        Break the narration into 5-10 second segments. For each segment provide the narration
        text and a *realistic, exactly 5-word* image search prompt (something findable on stock
        photo sites). audio_script and visual_script MUST have the same number of segments.

        Output ONLY this JSON structure:
        {{
            "topic": "{topic}",
            "description": "one-line description",
            "audio_script": [
                {{"timestamp": "00:00", "text": "narration for this segment",
                  "speaker": "default", "speed": 1.0, "pitch": 1.0, "emotion": "neutral"}}
            ],
            "visual_script": [
                {{"timestamp_start": "00:00", "timestamp_end": "00:05",
                  "prompt": "five word realistic image prompt",
                  "negative_prompt": "abstract, blurry, text, watermark"}}
            ]
        }}"""

        raw_output = self._generate_content(prompt, self.system_prompt_segmentation)
        script = self._extract_json(raw_output)

        if not script.get('topic'):
            script['topic'] = topic

        # Guard: the downstream audio/image steps require audio_script. Fail loudly.
        if not script.get('audio_script'):
            raise RuntimeError(
                "Script generation did not produce an 'audio_script'. "
                f"Parsed keys: {list(script.keys())}. "
                f"Raw model output (truncated): {raw_output[:500]}"
            )

        return script

    def save_script(self, script: Dict, filename: str) -> None:
        with open(filename, 'w') as f:
            json.dump(script, f, indent=2)
            print(f"Script saved to {filename}")