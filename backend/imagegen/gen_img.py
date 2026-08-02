"""
Image Sourcing Module - Path-Agnostic Version

Default flow (IMAGE_PROVIDER=auto):
  1. Pexels stock photo search - the PRIMARY source. Real photography suits real
     news subjects, and it is one fast, free HTTP GET.
  2. Google Gemini image generation - the FALLBACK, used only for scenes where
     Pexels has no match.

`IMAGE_PROVIDER` selects a different strategy:
  auto      - Pexels -> Gemini            (default)
  genblaze  - Genblaze -> Pexels -> Gemini (opt-in generative visuals)
  pexels    - Pexels only
  gemini    - Gemini only

All paths are passed as parameters - no hardcoded paths.
"""
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

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


# Words that are either grammatical filler or name something that cannot be
# photographed. Keeping them in a fallback query wastes the retry: "despite
# uncertainties india" matches nothing useful, while "india" matches plenty.
_UNSEARCHABLE = {
    "the", "and", "for", "with", "from", "that", "this", "into", "over", "amid",
    "despite", "after", "before", "while", "about", "than", "then", "under",
    "measure", "concept", "abstract", "outlook", "sentiment", "volatility",
    "uncertainty", "uncertainties", "growth", "momentum", "trend", "trends",
    "data", "analysis", "report", "reading", "level", "levels", "index",
}


def _trim_query(query: str) -> str:
    """
    Shorten a query to its most searchable core.

    Pexels matches all terms, so a long phrase can return nothing while its head
    nouns return plenty. Drops filler and unphotographable abstractions, then
    keeps up to three remaining words.
    """
    words = [w for w in re.findall(r"[A-Za-z']+", query) if len(w) > 2]
    meaningful = [w for w in words if w.lower() not in _UNSEARCHABLE]
    return " ".join((meaningful or words)[:3])


def _query_terms(query: str) -> set:
    return {
        w.lower() for w in re.findall(r"[A-Za-z']+", query)
        if len(w) > 2 and w.lower() not in _UNSEARCHABLE
    }


def _relevance(query: str, candidate: dict) -> int:
    """
    How many meaningful words of the query the photo's own description echoes.

    Neither stock API ever returns nothing — each returns its loosest match and
    says nothing about how loose it was, which is how "Indian rupee banknotes"
    ended up as Turkish lira. Both attach a human-written description to every
    photo, so overlapping content words are a cheap, honest signal of whether a
    result is on topic at all, and a directly comparable one across providers.
    """
    haystack = (candidate.get("text") or "").lower()
    if not haystack.strip():
        # No description to judge by. Treat as a weak match rather than a
        # disqualification, so photos without a caption stay usable.
        return 1
    return sum(1 for t in _query_terms(query) if t in haystack)


def _pexels_candidates(query: str, api_key: str, orientation: str) -> List[dict]:
    """Pexels results, normalised to the shared candidate shape."""
    if not api_key:
        return []
    try:
        r = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": api_key},
            params={"query": query, "per_page": 8, "orientation": orientation},
            timeout=30,
        )
        r.raise_for_status()
        out = []
        for p in r.json().get("photos", []):
            url = p["src"].get("large2x") or p["src"].get("large")
            if url:
                out.append({
                    "id": f"pexels:{p.get('id')}",
                    "url": url,
                    "text": p.get("alt") or "",
                    "source": "pexels",
                })
        return out
    except Exception as e:  # noqa: BLE001 - the other provider may still deliver
        print(f"Pexels lookup failed for '{query}': {e}")
        return []


def _unsplash_candidates(query: str, api_key: str, orientation: str) -> List[dict]:
    """
    Unsplash results, normalised to the shared candidate shape.

    A second library matters more for relevance than for volume: the two have
    very different coverage, and a query that only Pexels answers with filler is
    often answered properly by Unsplash (and the reverse). Scoring both pools
    against the query and taking the better match is what keeps scenes on topic.
    """
    if not api_key:
        return []
    try:
        r = requests.get(
            "https://api.unsplash.com/search/photos",
            headers={"Authorization": f"Client-ID {api_key}",
                     "Accept-Version": "v1"},
            params={"query": query, "per_page": 8, "orientation": orientation,
                    "content_filter": "high"},
            timeout=30,
        )
        r.raise_for_status()
        out = []
        for p in r.json().get("results", []):
            url = (p.get("urls") or {}).get("regular") or (p.get("urls") or {}).get("full")
            if not url:
                continue
            # Unsplash splits its caption across two optional fields and also
            # exposes the tags it indexed the photo under; all three describe the
            # subject, so all three feed the relevance score.
            tags = " ".join(t.get("title", "") for t in (p.get("tags") or []))
            out.append({
                "id": f"unsplash:{p.get('id')}",
                "url": url,
                "text": " ".join(filter(None, [
                    p.get("alt_description"), p.get("description"), tags,
                ])),
                "source": "unsplash",
            })
        return out
    except Exception as e:  # noqa: BLE001
        print(f"Unsplash lookup failed for '{query}': {e}")
        return []


# Unsplash uses different orientation words than Pexels for the same shapes.
_UNSPLASH_ORIENTATION = {"portrait": "portrait", "landscape": "landscape", "square": "squarish"}


def _with_shorter_query(fetch, query: str, trimmed: str, api_key: str, orientation: str) -> List[dict]:
    """
    Run one provider's search, retrying with the trimmed query if it draws a blank.

    The two libraries have very different tolerance for long phrases. Unsplash
    requires every term to match, so a six-word scene prompt returned ZERO
    results from it every time while Pexels answered the same prompt with eight
    — which meant Unsplash was never actually contributing to the ranking. The
    per-provider retry gives each library a query it can answer, so the
    relevance comparison is between two real pools rather than one.
    """
    if not api_key:
        return []
    results = fetch(query, api_key, orientation)
    if not results and trimmed and trimmed != query:
        results = fetch(trimmed, api_key, orientation)
    return results


def _identifying_terms(query: str) -> List[str]:
    """
    The words that decide whether a shot is the right one.

    Capitalised tokens in a scene prompt are the proper nouns the script chose to
    pin the shot down — "Bombay", "Bengaluru", "Tata", "Sensex" — plus the head
    noun the prompt opens with. Drop one of those and the footage can be of the
    right *kind* of thing in entirely the wrong place.
    """
    words = re.findall(r"[A-Za-z']+", query)
    return [
        w.lower() for w in words
        if len(w) > 2 and w[0].isupper() and w.lower() not in _UNSEARCHABLE
    ]


# Words that describe framing rather than subject. A clip matching only these
# has told you nothing: "modern french architecture building exterior" shares
# "building" and "exterior" with a prompt about the Bombay Stock Exchange and is
# still a building in France.
_SCENERY = {
    "building", "buildings", "exterior", "interior", "close", "view", "shot",
    "scene", "background", "modern", "aerial", "footage", "closeup", "wide",
}


# Places that show up in stock-footage slugs. A clip naming one of these when the
# prompt does not is footage of somewhere else — which the relaxed tier would
# otherwise wave through: "the british flag waving at the new york stock
# exchange" matched "stock" and "exchange" and opened a video about the Sensex.
_PLACES = {
    "american", "america", "usa", "york", "british", "britain", "england",
    "london", "french", "france", "paris", "german", "germany", "berlin",
    "chinese", "china", "beijing", "shanghai", "japan", "japanese", "tokyo",
    "russia", "russian", "moscow", "italy", "italian", "rome", "spain",
    "spanish", "brazil", "brazilian", "mexico", "mexican", "canada",
    "canadian", "australia", "australian", "dubai", "singapore", "european",
    "europe", "african", "korea", "korean", "turkish", "turkey", "argentine",
    "philippine", "peso", "dollar", "euro", "pound",
}


def _place_conflict(query: str, slug_words: List[str]) -> bool:
    """True when the clip names a place or currency the prompt never mentioned."""
    asked = _query_terms(query)
    return any(w in _PLACES and w not in asked for w in slug_words)


def _term_in(term: str, words: List[str]) -> bool:
    """
    Whether `term` appears in `words`, allowing for inflection.

    A literal comparison rejected "interactive trading desk" for the term
    "trader" and "india note" for "indian" — the same subject under a different
    ending. Matching on a shared four-character stem recovers those without
    loosening the check where it matters: no stem of "bombay" reaches "new york".
    """
    for w in words:
        if w == term:
            return True
        if min(len(w), len(term)) >= 5:
            common = 0
            for a, b in zip(w, term):
                if a != b:
                    break
                common += 1
            if common >= 4:
                return True
    return False


def _slug_text(page_url: str) -> str:
    """
    The descriptive words out of a Pexels video page URL.

    Video results carry no caption and an empty `tags` array, but the page slug
    is written from the subject — ".../video/a-person-tossing-money-4836695/".
    Without it there would be no way to tell an on-topic clip from a loose one,
    and Pexels video is as loose as Pexels photo: "indian rupee banknotes"
    returns Philippine peso footage.
    """
    if not page_url:
        return ""
    slug = page_url.rstrip("/").rsplit("/", 1)[-1]
    return " ".join(w for w in slug.split("-") if not w.isdigit())


def _pexels_video_candidates(
    query: str,
    api_key: str,
    orientation: str,
    min_height: int = 854,
) -> List[dict]:
    """Pexels video results, normalised to the shared candidate shape."""
    if not api_key:
        return []
    try:
        r = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": api_key},
            params={"query": query, "per_page": 8, "orientation": orientation,
                    "size": "small"},
            timeout=30,
        )
        r.raise_for_status()
        out = []
        for v in r.json().get("videos", []):
            files = [f for f in v.get("video_files", [])
                     if f.get("file_type") == "video/mp4" and f.get("link")]
            if not files:
                continue
            # Smallest rendition that still covers the output frame. The source
            # masters are 4K; downloading one per scene would dominate both the
            # render time and the memory ceiling for no visible gain at 480x854.
            files.sort(key=lambda f: f.get("height") or 0)
            chosen = next((f for f in files if (f.get("height") or 0) >= min_height), files[-1])
            out.append({
                "id": f"pexels-video:{v.get('id')}",
                "url": chosen["link"],
                "text": _slug_text(v.get("url") or ""),
                "source": "pexels-video",
                "duration": v.get("duration") or 0,
            })
        return out
    except Exception as e:  # noqa: BLE001 - stills are the fallback
        print(f"Pexels video lookup failed for '{query}': {e}")
        return []


def search_stock_video(
    query: str,
    pexels_api_key: str = "",
    aspect_ratio: str = "9:16",
    exclude_ids: Optional[set] = None,
    min_height: int = 854,
    relax: bool = False,
    strict_place: bool = True,
) -> Tuple[Optional[bytes], Optional[str]]:
    """
    Find an on-topic stock video clip for `query` and return (bytes, clip_id).

    Deliberately stricter than the photo search: a clip is used ONLY when its
    own slug echoes the query. Video results have no caption to fall back on and
    the library is far smaller than the photo one, so an unranked "best video
    match" is much more likely to be unrelated footage than an unranked photo
    is. When nothing scores, this returns nothing and the caller uses a still —
    a correct photograph beats moving footage of the wrong subject.
    """
    if not pexels_api_key:
        return None, None

    used = exclude_ids or set()
    orientation = _orientation_for(aspect_ratio)
    trimmed = _trim_query(query)

    candidates = _with_shorter_query(
        lambda q, k, o: _pexels_video_candidates(q, k, o, min_height),
        query, trimmed, pexels_api_key, orientation,
    )
    # Every identifying word of the prompt must appear in the clip's slug — not
    # merely some overlap. A "some overlap" rule accepted footage of the NEW YORK
    # Stock Exchange, US and UK flags flying, for the prompt "Bombay Stock
    # Exchange building exterior": it matched "stock exchange building" and lost
    # the only word that made the shot right or wrong. The video library is a
    # fraction of the size of the photo one, so a loose match there is far more
    # likely to be the wrong country entirely.
    required = _identifying_terms(query)
    # `relax` is used for the opening scenes only, and only after the strict pass
    # has found nothing. It asks for a clip that is about the right SUBJECT rather
    # than the right place: at least one content word that is not mere framing.
    #
    # This is not a loosening of standards so much as consistency with the photo
    # path, which already answers "Bombay Stock Exchange building exterior" with a
    # New York Stock Exchange facade. Refusing the equivalent clip bought nothing
    # — the same wrong-city subject, just static and less watchable.
    subject_terms = [t for t in _query_terms(query) if t not in _SCENERY]
    pool = []
    for c in candidates:
        if c["id"] in used:
            continue
        slug_words = re.findall(r"[a-z']+", (c.get("text") or "").lower())
        if relax:
            if not any(_term_in(t, slug_words) for t in subject_terms):
                continue
            # Right kind of thing, possibly wrong country. Ranked last rather
            # than discarded: rejecting outright forfeited the slot, which put a
            # still on the opening scene — the one place motion actually changes
            # whether a viewer stays. A correct-place clip still always wins when
            # one exists. SCENE_VIDEO_STRICT_PLACE=true drops these instead.
            conflicted = _place_conflict(query, slug_words)
            if conflicted and strict_place:
                print(f"  skipped clip {c['text'][:44]!r}: names another place")
                continue
            c["_place_conflict"] = conflicted
        else:
            missing = [t for t in required if not _term_in(t, slug_words)]
            if missing:
                print(f"  skipped clip {c['text'][:44]!r}: missing {missing}")
                continue
        pool.append(c)

    scored = [(max(_relevance(query, c), _relevance(trimmed, c)), c) for c in pool]
    if not scored:
        return None, None

    # Clean matches first, then by how much of the query they echo.
    scored.sort(key=lambda pair: (pair[1].get("_place_conflict", False), -pair[0]))
    for score, cand in scored:
        try:
            r = requests.get(cand["url"], timeout=60)
            r.raise_for_status()
            tier = "relaxed" if relax else "exact"
            print(f"  pexels-video match ({tier}, {score}): {cand['text'][:56]!r} [{cand['duration']}s]")
            return r.content, cand["id"]
        except Exception as e:  # noqa: BLE001 - try the next clip
            print(f"Pexels video download failed ({cand['id']}): {e}")
    return None, None


def search_stock_image(
    query: str,
    pexels_api_key: str = "",
    unsplash_api_key: str = "",
    aspect_ratio: str = "9:16",
    exclude_ids: Optional[set] = None,
) -> Tuple[Optional[bytes], Optional[str]]:
    """
    Find the most on-topic stock photo for `query` and return (bytes, photo_id).

    Searches Pexels and Unsplash together and ranks the combined pool by
    relevance rather than trusting either provider's own ordering — both return
    their loosest match rather than nothing, so "best result from one library"
    is a much weaker guarantee than "best result across two, scored against the
    query". Providers are queried concurrently, so a second library costs
    latency only when one of them is slow.

    Asks for several candidates per provider so that a photo already used by
    another scene can be skipped (with one candidate, two similar prompts
    returned the same photo and it appeared twice in one video) and a failed
    download falls through instead of dropping the scene to generation.

    Retries once with a trimmed query when nothing on-topic comes back.

    Returns:
        (image bytes, photo id), or (None, None) if nothing usable was found.
        The id is namespaced by provider, since the two number their photos
        independently and a bare integer would collide across them.
    """
    if not pexels_api_key and not unsplash_api_key:
        return None, None

    used = exclude_ids or set()
    orientation = _orientation_for(aspect_ratio)
    trimmed = _trim_query(query)

    for attempt_query in (query, trimmed):
        if not attempt_query:
            continue

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(_with_shorter_query, _pexels_candidates, attempt_query,
                            trimmed, pexels_api_key, orientation),
                pool.submit(_with_shorter_query, _unsplash_candidates, attempt_query,
                            trimmed, unsplash_api_key,
                            _UNSPLASH_ORIENTATION.get(orientation, "portrait")),
            ]
            candidates = [c for f in futures for c in f.result()]

        if not candidates:
            continue

        # Prefer unused photos; fall back to the whole pool if every candidate is
        # taken, since a repeat beats an empty scene.
        pool_ = [c for c in candidates if c["id"] not in used] or candidates
        pool_.sort(key=lambda c: -_relevance(attempt_query, c))
        relevant = [c for c in pool_ if _relevance(attempt_query, c) > 0]
        if not relevant and attempt_query != trimmed:
            # Neither library has anything matching this phrasing. A shorter
            # query usually has a far better pool, so try that before settling
            # for a photo that has nothing to do with the story.
            continue

        for cand in (relevant or pool_):
            try:
                r = requests.get(cand["url"], timeout=30)
                r.raise_for_status()
                print(f"  {cand['source']} match ({_relevance(attempt_query, cand)}): {cand['text'][:60]!r}")
                return r.content, cand["id"]
            except Exception as e:  # noqa: BLE001 - try the next candidate
                print(f"{cand['source']} download failed ({cand['id']}): {e}")

    return None, None


def search_pexels_image(
    query: str,
    api_key: str,
    aspect_ratio: str = "1:1",
    exclude_ids: Optional[set] = None,
) -> Tuple[Optional[bytes], Optional[str]]:
    """Pexels-only search. Kept for callers that pin a single provider."""
    return search_stock_image(
        query, pexels_api_key=api_key, aspect_ratio=aspect_ratio, exclude_ids=exclude_ids
    )


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
    aspect_ratio: str = "9:16",
    unsplash_api_key: str = "",
    gemini_model: str = "gemini-2.5-flash-image",
    used_ids: Optional[set] = None,
    used_lock: Optional[threading.Lock] = None,
) -> Optional[bytes]:
    """
    Get an image for a prompt: search the stock libraries, fall back to Gemini.

    `used_ids` is shared across the scene thread pool so no two scenes in the same
    video land on the same stock photo. It is claimed under `used_lock` before the
    bytes are returned, so two threads racing on similar prompts cannot both take
    the same candidate.

    Returns:
        Image bytes, or None if both sources fail.
    """
    snapshot = set()
    if used_ids is not None and used_lock is not None:
        with used_lock:
            snapshot = set(used_ids)

    image_bytes, photo_id = search_stock_image(
        prompt, pexels_api_key, unsplash_api_key, aspect_ratio, exclude_ids=snapshot
    )
    if image_bytes:
        if used_ids is not None and used_lock is not None and photo_id is not None:
            with used_lock:
                used_ids.add(photo_id)
        return image_bytes

    print(f"No stock match for '{prompt}', falling back to Gemini...")
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
    unsplash_api_key: str = "",
    gemini_model: str = "gemini-2.5-flash-image",
    aspect_ratio: str = "9:16",
    video_clips: bool = False,
    video_min_height: int = 854,
    video_strict_place: bool = True,
    video_max_clips: int = 2,
    image_provider: str = "auto",
    genblaze_generator: Optional[Callable[[List[str], List[Path], str], Sequence[Optional[Path]]]] = None,
    source_log: Optional[dict] = None,
) -> bool:
    """
    Process the script JSON and source one image per scene.

    Args:
        script_path: Path to the script JSON file
        images_output_path: Directory where images should be saved
        gemini_api_key: Gemini API key (direct fallback image generation)
        pexels_api_key: Pexels API key (stock image source)
        unsplash_api_key: Unsplash access key (second stock source; optional).
            When set, both libraries are searched and the more on-topic result
            wins — a query one library only answers with filler is often
            answered properly by the other.
        gemini_model: Gemini image model used on fallback
        aspect_ratio: Desired image aspect ratio
        image_provider: "auto" (stock -> gemini, the default flow), "genblaze"
            (genblaze -> stock -> gemini), or a pinned source ("pexels",
            "unsplash", "gemini")
        genblaze_generator: callable(prompts, out_paths, aspect_ratio) returning
            a path per prompt (None where that scene failed). Supplied by the
            video service so this module stays free of app imports.
        video_clips: when True, each scene first tries for a short stock VIDEO
            clip and only falls back to a still when no clip is genuinely on
            topic. Motion holds attention far better than a static frame, but a
            correct photograph still beats footage of the wrong subject.
        video_min_height: smallest clip rendition to accept, in pixels. The
            source masters are 4K; capping this keeps render time and peak
            memory near the still-image baseline.
        video_max_clips: how many scenes may use video at most. Every scene clip
            holds an ffmpeg decoder open for the whole write — roughly 125 MB
            each — so an unbounded count made peak memory a function of video
            length, and a 60-second render OOM-killed the container. Scenes past
            the cap use stills, which cost almost nothing.
        source_log: optional dict mutated with which source served each scene —
            the render's provenance record reads this back.

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
    provider = (image_provider or "auto").lower()
    sources: dict = source_log if source_log is not None else {}

    # Resolve one output path per scene up front so every source writes to the
    # same place and "which scenes are still missing" is a simple exists() check.
    targets: List[Path] = []
    prompts: List[str] = []
    for idx, scene in enumerate(scenes):
        timestamp = scene.get("timestamp_start", f"{idx:03d}")
        scene_id = str(timestamp).replace(":", "-")
        targets.append(images_output_path / f"scene_{scene_id}.jpg")
        prompts.append(scene.get("prompt") or "")

    # --- Pass 1: Genblaze generation (opt-in only) -----------------------
    # NOT part of "auto": Pexels is the primary source by design — real stock
    # photography suits real news subjects better than a synthesized frame, and
    # it is a single fast HTTP GET. Genblaze generation runs only when the
    # operator asks for it with IMAGE_PROVIDER=genblaze, and still falls back to
    # Pexels then Gemini for any scene it cannot produce.
    if genblaze_generator and provider == "genblaze":
        wanted = [(i, p) for i, p in enumerate(prompts) if p]
        if wanted:
            try:
                produced = genblaze_generator(
                    [p for _, p in wanted],
                    [targets[i] for i, _ in wanted],
                    aspect_ratio,
                )
                for (idx, _), path in zip(wanted, produced or []):
                    if path and Path(path).exists():
                        sources[str(idx)] = "genblaze"
            except Exception as e:  # noqa: BLE001 - fall through to stock/direct
                print(f"Genblaze image generation unavailable: {e}")

    # --- Pass 2: Pexels / direct Gemini for anything still missing --------
    # Shared across the thread pool so two scenes never claim the same stock
    # photo. Previously each scene asked for per_page=1 independently, so similar
    # prompts produced the identical frame twice in one video.
    used_photo_ids: set = set()
    used_lock = threading.Lock()
    clips_claimed = {"n": 0}

    def _source_one(idx: int) -> bool:
        target = targets[idx]
        if target.exists() and target.stat().st_size > 0:
            return True  # already produced by an earlier pass
        # A scene that already has a .mp4 is deliberately NOT skipped: the clip
        # plays once and the photograph holds the rest of the segment, so a clip
        # scene needs both files.

        prompt = prompts[idx]
        if not prompt:
            print(f"Scene {idx}: Missing prompt, skipping")
            return False

        try:
            print(f"Sourcing visual for prompt: {prompt}")
            image_bytes = None
            used = None

            # Stock photography first (primary source), then Gemini generation
            # as the fallback. "genblaze" mode reaches here only for scenes
            # Genblaze could not produce, so it gets the same safety net.
            if provider in ("auto", "genblaze", "pexels", "unsplash"):
                with used_lock:
                    snapshot = set(used_photo_ids)
                image_bytes, photo_id = search_stock_image(
                    prompt,
                    pexels_api_key="" if provider == "unsplash" else pexels_api_key,
                    unsplash_api_key="" if provider == "pexels" else unsplash_api_key,
                    aspect_ratio=aspect_ratio,
                    exclude_ids=snapshot,
                )
                if image_bytes:
                    # Report the library that actually served the scene, not the
                    # configured preference — the provenance manifest reads this.
                    used = photo_id.split(":", 1)[0] if photo_id else "stock"
                    if photo_id is not None:
                        with used_lock:
                            used_photo_ids.add(photo_id)

            if not image_bytes and provider in ("auto", "genblaze", "gemini"):
                image_bytes = generate_gemini_image(prompt, gemini_api_key, gemini_model)
                if image_bytes:
                    used = "gemini"

            if not image_bytes:
                print(f"No image obtained for prompt: {prompt}")
                return False

            if save_image(image_bytes, target):
                sources[str(idx)] = used
                return True
            return False
        except Exception as e:  # noqa: BLE001 - one scene shouldn't kill the batch
            print(f"Error processing scene {idx}: {e}")
            return False

    # --- Pass 2a: motion, for the OPENING scenes only ---------------------
    #
    # Sequential and in scene order, unlike the still pass below. Two reasons:
    #
    #  * Placement. A viewer decides within a second or two whether to keep
    #    watching, so the moving footage has to be at the front. Claiming clips
    #    from a thread pool put them wherever a worker happened to finish, which
    #    routinely left the opening on a static photograph and the motion buried
    #    at 0:40 where it changes nothing.
    #  * The cap. One counter with no lock contention, and no downloading a clip
    #    only to delete it because another thread took the last slot.
    #
    # The window stops a few scenes in: past that a clip is no longer "the
    # opening", and the memory it costs is better spent not being spent.
    if video_clips and provider in ("auto", "genblaze", "pexels"):
        window = min(len(scenes), video_max_clips + 2)
        for idx in range(window):
            if clips_claimed["n"] >= video_max_clips:
                break
            prompt = prompts[idx]
            target = targets[idx]
            if not prompt or target.exists() or target.with_suffix(".mp4").exists():
                continue
            # Exact first, then relaxed, per scene. Ordering it this way — rather
            # than exact across every scene and only then relaxed — is what keeps
            # motion at the FRONT: a relaxed match on scene 0 beats an exact one
            # on scene 3, because scene 3 is past the point where the viewer has
            # already decided.
            clip_bytes, clip_id = search_stock_video(
                prompt, pexels_api_key, aspect_ratio,
                exclude_ids=used_photo_ids, min_height=video_min_height,
            )
            if not clip_bytes:
                clip_bytes, clip_id = search_stock_video(
                    prompt, pexels_api_key, aspect_ratio,
                    exclude_ids=used_photo_ids, min_height=video_min_height,
                    relax=True, strict_place=video_strict_place,
                )
            if not clip_bytes:
                continue
            if save_image(clip_bytes, target.with_suffix(".mp4")):
                clips_claimed["n"] += 1
                used_photo_ids.add(clip_id)
                sources[str(idx)] = "pexels-video"
        print(f"Motion scenes: {clips_claimed['n']} of the first {window} (cap {video_max_clips}).")

    # --- Pass 2b: stills for everything still missing ---------------------
    # Fetch remaining scenes CONCURRENTLY (Pexels is a plain HTTP GET, so a
    # sequential loop with sleeps was the bottleneck). Bounded pool keeps the
    # Gemini image fallback from hammering the API.
    max_workers = min(4, max(1, len(scenes)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        results = list(pool.map(_source_one, range(len(scenes))))

    success_count = sum(1 for ok in results if ok)
    print(f"Image sourcing completed. {success_count}/{len(scenes)} images obtained.")
    return success_count > 0
