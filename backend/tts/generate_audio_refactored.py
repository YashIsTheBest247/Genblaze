"""
Audio Generation Module - Path-Agnostic Version
Generates audio using Kokoro TTS pipeline
All paths are passed as parameters - no hardcoded paths
"""
import json
import io
import soundfile as sf
import os
from pathlib import Path
from typing import List, Dict, Any
from kokoro.pipeline import KPipeline


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
    lang_code: str = "b"
) -> List[Path]:
    """
    Main function to generate audio from script.
    
    Args:
        script_path: Path to the script JSON file
        audio_path: Directory where audio files should be saved
        lang_code: Language code for TTS
        
    Returns:
        List of paths to generated audio files
    """
    # Ensure paths are Path objects
    script_path = Path(script_path)
    audio_path = Path(audio_path)
    
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
    
    # Generate audio
    print(f"Generating audio from script: {script_path}")
    audio_bytes_list = generate_audio(script_data, lang_code)
    
    # Save audio files
    audio_files = merge_audio(audio_path, audio_bytes_list)
    
    print(f"Audio generation complete! Generated {len(audio_files)} files in {audio_path}")
    return audio_files
