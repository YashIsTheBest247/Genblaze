import json
import re
import time
import requests
from bs4 import BeautifulSoup
from typing import Dict, List, Optional
from google import genai
from google.genai import types

# Gemini's free tier caps generate_content per PROJECT PER DAY (20 requests), and
# a render spends two. So the limit that actually stops a day's work is not the
# per-minute rate limit — it is the daily one, and no amount of backoff clears it.
# Only a different project's key does. These markers identify that case so it can
# be told apart from a transient 429 that retrying will fix.
_PER_DAY_QUOTA_MARKERS = (
    "PerDay",
    "per day",
    "GenerateRequestsPerDayPerProject",
)


class GeminiKeyPool:
    """
    Rotates through Gemini API keys as each one's daily quota runs out.

    Holds no timers and no reset logic: a key marked exhausted stays exhausted for
    the life of the process. Google resets the daily counter on its own schedule
    (Pacific midnight), and guessing that boundary locally would send renders back
    to a key that is still empty. A restart re-arms every key.
    """

    def __init__(self, keys: List[str]):
        seen = []
        for key in keys or []:
            key = (key or "").strip()
            if key and key not in seen:
                seen.append(key)
        self.keys: List[str] = seen
        self._index = 0
        self._exhausted: set = set()

    def __bool__(self) -> bool:
        return bool(self.keys)

    def current(self) -> Optional[str]:
        """The key to use now, or None when every key is spent."""
        if self._index >= len(self.keys):
            return None
        return self.keys[self._index]

    def mark_exhausted(self) -> Optional[str]:
        """
        Retire the current key and return the next usable one (None if none left).
        """
        spent = self.current()
        if spent:
            self._exhausted.add(spent)
        self._index += 1
        return self.current()

    def status(self) -> Dict:
        """Remaining capacity, for logging and /health."""
        return {
            "configured": len(self.keys),
            "available": max(0, len(self.keys) - len(self._exhausted)),
            "exhausted": len(self._exhausted),
        }


class VideoScriptGenerator:
    def __init__(
        self,
        api_key: str = None,
        model: str = "gemini-2.5-flash",
        provider: str = "gemini",
        ollama_model: str = "llama3.2",
        ollama_base_url: str = "http://localhost:11434",
        api_keys: Optional[List[str]] = None,
    ):
        self.provider = (provider or "gemini").lower()
        self.model = model
        self.ollama_model = ollama_model
        self.ollama_base_url = ollama_base_url.rstrip("/")
        # `api_keys` supersedes `api_key` when given; `api_key` alone still works
        # so existing callers need no change.
        self.key_pool = GeminiKeyPool(api_keys if api_keys else ([api_key] if api_key else []))
        # Only create the Gemini client when actually using Gemini AND a key is
        # present. A missing key must not crash construction — the service is
        # instantiated at import time, and the app has to boot far enough to
        # report the missing key on /health instead of failing to start.
        self.client = None
        if self.provider == "gemini":
            self._activate(self.key_pool.current())

        self.system_prompt_segmentation = """
        You are a professional video script segmenter.  
        Your task is to take an existing video script draft and break it down into precise, timestamped segments for both audio and visuals, adhering to strict formatting and parameter guidelines.
        Rules for Segmentation:
        
        1. Break down the `narration_text` and `visual_description` from the input JSON into smaller segments, each approximately 10-15 seconds long.
        2. Generate timestamps ("00:00", "00:15", "00:30", etc.) for each segment in both `audio_script` and `visual_script`.
        3. Maintain *strict synchronization*: The `timestamp` values *must* be identical for corresponding audio and visual segments, and the number of segments in audio_script *must be same* as the number of segments in visual_script.
        4. For each visual segment, turn the `visual_description` into a `prompt` for a stock-photo search: a concrete, photographable subject in 3-6 words. Include a corresponding `negative_prompt`.
        5. The subject of a `prompt` must be a physical thing — a place, object, or person doing something. Abstract nouns ("volatility", "sentiment", "growth", "uncertainty") are not photographable and make the search return generic filler; swap them for a physical stand-in. Name real companies, indices, institutions and cities when the story mentions them, and vary the subject across segments.
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

    def _activate(self, key: Optional[str]) -> bool:
        """Point the client at `key`. False when there is no key left to use."""
        if not key:
            self.client = None
            return False
        self.client = genai.Client(api_key=key)
        return True

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

        if self.client is None:
            raise RuntimeError(
                "No script model configured. Set GEMINI_API_KEY, or set "
                "SCRIPT_PROVIDER=ollama to generate scripts locally."
            )

        transient_markers = ("503", "429", "500", "UNAVAILABLE", "overloaded", "high demand", "RESOURCE_EXHAUSTED")
        last_error = None
        # Counted by hand rather than with `for attempt in range(...)`: rotating to
        # a fresh key is not a retry of a failed attempt, it is the first attempt on
        # new quota, so it must not spend one of the budgeted retries.
        attempt = 0
        while attempt < max_retries:
            try:
                config_kwargs = dict(
                    system_instruction=system_prompt,
                    temperature=0.7,
                    max_output_tokens=8192,
                    response_mime_type="application/json",
                )
                # Disable Flash "thinking" — 2-3x lower latency; the JSON mime
                # type keeps the output on-spec. Ignored if the SDK/model lacks it.
                try:
                    config_kwargs["thinkingConfig"] = types.ThinkingConfig(thinkingBudget=0)
                except Exception:  # noqa: BLE001
                    pass

                response = self.client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(**config_kwargs),
                )
                return response.text
            except Exception as e:  # noqa: BLE001
                last_error = e
                msg = str(e)

                # Daily quota gone on this key. Backoff cannot help — only another
                # project's key can — so rotate before the transient check, which
                # would otherwise sleep through a 429 that never clears.
                if any(m in msg for m in _PER_DAY_QUOTA_MARKERS):
                    next_key = self.key_pool.mark_exhausted()
                    status = self.key_pool.status()
                    if self._activate(next_key):
                        print(
                            "Gemini daily quota exhausted on this key; switching to "
                            f"key {status['exhausted'] + 1} of {status['configured']}."
                        )
                        continue
                    raise RuntimeError(
                        "Every Gemini key has hit its daily free-tier quota "
                        f"({status['configured']} configured). Add another key to "
                        "GEMINI_API_KEYS, wait for the daily reset, or set "
                        "SCRIPT_PROVIDER=ollama to generate scripts locally."
                    )

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
                    attempt += 1
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

        Break the narration into 5-10 second segments. audio_script and visual_script MUST
        have the same number of segments.

        IMAGE PROMPTS — this is where scripts usually go wrong. Each `prompt` is fed to a
        stock-photo search, which always returns *something*; a vague query returns a
        generic office or skyscraper photo that has nothing to do with the story. So:

        - Name a CONCRETE, PHOTOGRAPHABLE subject: a place, object, person doing something,
          or document. 3 to 6 words.
        - NEVER use an abstract noun as the subject. "volatility", "sentiment", "outlook",
          "uncertainty", "growth", "momentum" cannot be photographed. Replace them with a
          physical stand-in: a trading floor screen, a newspaper front page, a bank counter.
        - When the story names a real company, index, institution or city, put it in the
          prompt: "Bombay Stock Exchange building", "Reserve Bank of India facade",
          "Mumbai skyline at dusk", "Indian rupee banknotes close up".
        - Vary the subjects across segments. Do not repeat the same subject twice.

        Good:  "Reserve Bank of India building", "Indian rupee notes counting",
               "Mumbai traders watching screens", "oil refinery pipes sunset"
        Bad:   "market volatility concept", "business people reviewing data",
               "financial growth abstract", "economy uncertainty"

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
                  "prompt": "concrete photographable subject, 3-6 words",
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