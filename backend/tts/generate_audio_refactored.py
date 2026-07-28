"""
Audio Generation Module - Path-Agnostic Version

Three narration backends, tried in this order under TTS_PROVIDER=auto:

  1. Genblaze cloud TTS (GMI Cloud / ElevenLabs / MiniMax) - used when a cloud
     key is configured.
  2. Edge TTS - free, no API key, no local model. THE DEFAULT.
  3. Kokoro TTS - fully local, needs torch (~490 MB of deps, ~1.5 GB peak).

Why Edge is the default: Kokoro loads torch and downloads a ~350 MB model on
first use. On a container with an ephemeral disk that download happens during
the first render, concurrent with the torch load, and the memory spike
OOM-killed the deployment every single time. Edge TTS is a network call with
negligible memory, which is what lets this run on a small instance at all.

Kokoro remains fully supported for local runs (TTS_PROVIDER=kokoro), but its
dependencies are optional - see requirements-kokoro.txt. torch, soundfile and
kokoro are imported lazily inside that path only, so an image built without
them still runs everything else.

All paths are passed as parameters - no hardcoded paths.
"""
import asyncio
import json
import io
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

# Speaker label -> Edge TTS voice. The script LLM emits these three labels.
DEFAULT_VOICE_MALE = "en-GB-RyanNeural"
DEFAULT_VOICE_FEMALE = "en-GB-SoniaNeural"

# Bounded so a long script doesn't open dozens of sockets at once.
_EDGE_CONCURRENCY = 4


def generate_audio(
    script_data: Dict[str, Any],
    lang_code: str = "b"
) -> List[bytes]:
    """
    Generate audio from script data using Kokoro TTS.

    Args:
        script_data: Dictionary containing audio_script with segments
        lang_code: Language code for TTS (default: "b")

    Returns:
        List of audio bytes for each segment
    """
    # Imported here, not at module scope: this pulls in torch (~300 MB) and is
    # skipped entirely when narration comes from Genblaze cloud TTS.
    import soundfile as sf
    from kokoro.pipeline import KPipeline

    pipeline = KPipeline(lang_code=lang_code)

    all_audio = []
    for segment in script_data["audio_script"]:
        # Map speaker to voice ID
        speaker_id = "am_adam" if segment["speaker"] in ["default", "narrator_male"] else "af_heart"
        
        # Generate audio
        audio = pipeline(
            text=segment["text"],
            voice=speaker_id,
            speed=segment.get("speed", 1.0)
        )
        
        # Collect audio chunks
        buffer = io.BytesIO()
        for _, _, chunk in audio:
            sf.write(buffer, chunk, 24000, format='WAV')
        buffer.seek(0)
        all_audio.append(buffer.read())
    
    return all_audio


def _edge_voice_for(speaker: str, voice_male: str, voice_female: str) -> str:
    """Map the script's speaker label onto an Edge TTS voice."""
    return voice_male if speaker in ("default", "narrator_male") else voice_female


def _edge_rate(speed: Any) -> str:
    """
    Convert the script's speed multiplier (0.9-1.1) to an Edge rate string.

    Edge expects a signed percentage delta, e.g. 0.9 -> "-10%".
    """
    try:
        percent = int(round((float(speed) - 1.0) * 100))
    except (TypeError, ValueError):
        percent = 0
    percent = max(-50, min(100, percent))
    return f"{percent:+d}%"


def generate_audio_edge(
    script_data: Dict[str, Any],
    audio_path: Path,
    voice_male: str = DEFAULT_VOICE_MALE,
    voice_female: str = DEFAULT_VOICE_FEMALE,
) -> List[Path]:
    """
    Synthesize one MP3 per script segment using Edge TTS.

    Segments are generated concurrently (bounded), since each is an independent
    network round trip. Returns the audio paths in segment order.

    The assembly stage already accepts .mp3 and sorts on the `segment_N` stem,
    so emitting MP3 here needs no conversion step and no downstream change.

    Raises on any failure so the caller can fall back to another backend.
    """
    import edge_tts

    segments = script_data["audio_script"]
    audio_path = Path(audio_path)
    audio_path.mkdir(parents=True, exist_ok=True)
    targets = [audio_path / f"segment_{i}.mp3" for i in range(len(segments))]

    async def _run() -> None:
        semaphore = asyncio.Semaphore(_EDGE_CONCURRENCY)

        async def _one(index: int, segment: Dict[str, Any]) -> None:
            text = (segment.get("text") or "").strip()
            if not text:
                raise ValueError(f"Segment {index} has no text to narrate")
            async with semaphore:
                await edge_tts.Communicate(
                    text,
                    _edge_voice_for(segment.get("speaker", "default"), voice_male, voice_female),
                    rate=_edge_rate(segment.get("speed", 1.0)),
                ).save(str(targets[index]))

        await asyncio.gather(*(_one(i, s) for i, s in enumerate(segments)))

    # The render runs on a worker thread with no event loop of its own, so a
    # fresh loop here is safe and avoids touching the server's loop.
    asyncio.run(_run())

    missing = [t for t in targets if not t.exists() or t.stat().st_size == 0]
    if missing:
        raise RuntimeError(f"Edge TTS produced no audio for: {[m.name for m in missing]}")

    for target in targets:
        print(f"Audio file saved at: {target}")
    return targets


def merge_audio(
    audio_path: Path,
    audio_bytes_list: List[bytes]
) -> List[Path]:
    """
    Save audio segments to individual files.
    
    Args:
        audio_path: Directory where audio files should be saved
        audio_bytes_list: List of audio data as bytes
        
    Returns:
        List of paths to saved audio files
    """
    # Ensure output directory exists
    audio_path = Path(audio_path)
    audio_path.mkdir(parents=True, exist_ok=True)
    
    audio_files = []
    for idx, audio_bytes in enumerate(audio_bytes_list):
        output_path = audio_path / f"segment_{idx}.wav"
        with open(output_path, "wb") as f:
            f.write(audio_bytes)
        audio_files.append(output_path)
        print(f"Audio file: {idx} successfully saved at: {output_path}")
    
    return audio_files


def main_generate_audio(
    script_path: Path,
    audio_path: Path,
    lang_code: str = "b",
    genblaze_generator: Optional[Callable[[List[str], List[Path]], Sequence[Optional[Path]]]] = None,
    source_log: Optional[dict] = None,
    provider: str = "auto",
    voice_male: str = DEFAULT_VOICE_MALE,
    voice_female: str = DEFAULT_VOICE_FEMALE,
) -> List[Path]:
    """
    Main function to generate audio from script.

    Args:
        script_path: Path to the script JSON file
        audio_path: Directory where audio files should be saved
        lang_code: Language code for Kokoro TTS
        genblaze_generator: callable(texts, out_paths) returning a path per
            segment, used when cloud narration is configured
        source_log: optional dict mutated with the narration backend actually used
        provider: "auto" (genblaze -> edge -> kokoro), or pin one of
            "genblaze", "edge", "kokoro"
        voice_male / voice_female: Edge TTS voice names

    Returns:
        List of paths to generated audio files
    """
    # Ensure paths are Path objects
    script_path = Path(script_path)
    audio_path = Path(audio_path)
    audio_path.mkdir(parents=True, exist_ok=True)

    # Load script data
    try:
        with open(script_path, "r", encoding="utf-8") as f:
            script_data = json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Script file not found: {script_path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in script file: {e}")

    # Validate script structure
    if "audio_script" not in script_data:
        raise ValueError("Script file missing 'audio_script' key")

    segments = script_data["audio_script"]
    log = source_log if source_log is not None else {}
    choice = (provider or "auto").lower()

    def _discard(paths) -> None:
        """A partial narration would desync the subtitles — never keep one."""
        for path in paths:
            Path(path).unlink(missing_ok=True)

    # --- 1. Cloud narration via Genblaze ---------------------------------
    if genblaze_generator and choice in ("auto", "genblaze"):
        texts = [segment.get("text", "") for segment in segments]
        targets = [audio_path / f"segment_{idx}.wav" for idx in range(len(segments))]
        try:
            produced = genblaze_generator(texts, targets)
            done = [Path(p) for p in (produced or []) if p and Path(p).exists()]
            if len(done) == len(segments):
                log["provider"] = "genblaze"
                print(f"Narration complete via Genblaze: {len(done)} segments")
                return targets
            if done:
                print(f"Genblaze produced {len(done)}/{len(segments)} segments; "
                      "falling back for consistency.")
                _discard(done)
        except Exception as e:  # noqa: BLE001
            print(f"Genblaze narration unavailable ({e}).")

    # --- 2. Edge TTS (default) -------------------------------------------
    if choice in ("auto", "edge"):
        try:
            audio_files = generate_audio_edge(script_data, audio_path, voice_male, voice_female)
            log["provider"] = "edge"
            log["voice"] = f"{voice_male}/{voice_female}"
            print(f"Narration complete via Edge TTS: {len(audio_files)} segments")
            return audio_files
        except Exception as e:  # noqa: BLE001
            print(f"Edge TTS failed ({e}); falling back to local Kokoro TTS.")
            _discard(audio_path.glob("segment_*.mp3"))

    # --- 3. Local narration via Kokoro (optional deps) -------------------
    print(f"Generating audio from script: {script_path}")
    try:
        audio_bytes_list = generate_audio(script_data, lang_code)
    except ImportError as e:
        raise RuntimeError(
            "No narration backend available. Edge TTS failed and Kokoro is not "
            "installed — install it with: pip install -r requirements-kokoro.txt "
            f"(original error: {e})"
        ) from e
    audio_files = merge_audio(audio_path, audio_bytes_list)
    log["provider"] = "kokoro"

    print(f"Audio generation complete! Generated {len(audio_files)} files in {audio_path}")
    return audio_files
