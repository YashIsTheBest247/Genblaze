"""
Video Assembly Module - Path-Agnostic Version
Assembles final video from images, audio, and subtitles
All paths are passed as parameters - no hardcoded paths
"""
import os
import json
import shutil
from pathlib import Path
from typing import List, Optional
from moviepy import ImageClip, concatenate_videoclips, AudioFileClip, TextClip, CompositeVideoClip, vfx
import pysrt
from PIL import Image, ImageDraw, ImageFont

# Output resolution. Default is vertical 480p (480x854, 9:16) — fastest to encode
# (~1 min renders), fine for Shorts/mobile. Bump to 720x1280 or 1080x1920 via
# FLUX_VIDEO_WIDTH/HEIGHT for higher quality at the cost of render speed.
TARGET_W = int(os.environ.get("FLUX_VIDEO_WIDTH", "480"))
TARGET_H = int(os.environ.get("FLUX_VIDEO_HEIGHT", "854"))

# Text sizes scale with the output width so captions/titles never overflow or get
# clipped, whatever the resolution (480p -> 1080p).
SUBTITLE_BOX_W = int(TARGET_W * 0.90)
SUBTITLE_FONT_SIZE = max(14, int(TARGET_W * 0.055))
TITLE_FONT_SIZE = max(18, int(TARGET_W * 0.075))

# Vertical placement of the caption block, as a fraction of frame height (0 = top,
# 1 = bottom). A caption box has auto height and grows downward, so this is the
# position of its TOP edge — push it too far and a three-line caption runs off the
# bottom. 0.78 sits the block low in the frame while leaving room for wrapping.
SUBTITLE_Y = float(os.environ.get("FLUX_SUBTITLE_Y", "0.78"))

# Padding inside the caption's background box, and the gap between wrapped lines.
# Scaled off the font size so they hold at any output resolution.
SUBTITLE_PAD_X = max(8, int(SUBTITLE_FONT_SIZE * 0.45))
SUBTITLE_PAD_Y = max(6, int(SUBTITLE_FONT_SIZE * 0.34))
SUBTITLE_INTERLINE = max(4, int(SUBTITLE_FONT_SIZE * 0.28))


def make_subtitle_clip(text: str, font_path):
    """
    Build a caption clip sized relative to the output width.

    Height is auto (None) so long lines wrap and grow downward instead of being
    cut off, and the box always fits inside the frame.
    """
    return TextClip(
        text=text,
        font=str(font_path),
        color='white',
        bg_color='black',
        size=(SUBTITLE_BOX_W, None),
        font_size=SUBTITLE_FONT_SIZE,
        method='caption',
        text_align="center",
        horizontal_align="center",
        # Auto height came out as exactly lines x font_size, leaving no room for
        # descenders — the bottom line of a wrapped caption was visibly sliced
        # through ("India VIX, a key" lost its descenders). The margin pads the
        # background box; interline opens the gap between wrapped lines.
        margin=(SUBTITLE_PAD_X, SUBTITLE_PAD_Y),
        interline=SUBTITLE_INTERLINE,
    )


def fit_to_frame(clip, target_w: int = TARGET_W, target_h: int = TARGET_H):
    """Scale a clip to COVER the target frame, then center-crop to exact size.

    Keeps aspect ratio (no distortion) so mixed-size stock images all fill a
    consistent vertical 9:16 frame.
    """
    cw, ch = clip.size
    if not cw or not ch:
        return clip
    scale = max(target_w / cw, target_h / ch)
    new_w, new_h = round(cw * scale), round(ch * scale)
    clip = clip.resized((new_w, new_h))
    x1 = max(0, (new_w - target_w) // 2)
    y1 = max(0, (new_h - target_h) // 2)
    return clip.cropped(x1=x1, y1=y1, width=target_w, height=target_h)


def check_file_exists(file_path: Path) -> bool:
    """Check if a file exists at the specified path."""
    if file_path.is_file():
        return True
    else:
        raise FileNotFoundError(f"File not found: {file_path}")


def check_folder_exists(folder_path: Path) -> bool:
    """Checks if a folder path is valid."""
    if folder_path.is_dir():
        return True
    else:
        raise FileNotFoundError(f"Folder not found at {folder_path}")


def get_files(folder: Path, extensions: tuple) -> List[Path]:
    """
    Retrieves files with specified extensions from a folder.
    
    Args:
        folder: Path to the folder
        extensions: File extensions to include (e.g., ('.jpg', '.png'))
        
    Returns:
        List of file paths sorted numerically
    """
    folder = Path(folder)
    
    if not folder.is_dir():
        raise OSError(f"{folder} not found.")
    
    files = []
    for file in folder.iterdir():
        if file.is_file() and file.suffix.lower() in extensions:
            files.append(file)
    
    # Sort files numerically based on segment/scene number
    def extract_number(filepath: Path) -> int:
        try:
            base = filepath.stem
            # Handle both "segment_X" and "scene_X" naming conventions
            if base.startswith('segment_'):
                number_part = base.split('_')[1]
            elif base.startswith('scene_'):
                number_part = base.split('_')[1].split('-')[0]
            else:
                # For other files, extract any number
                import re
                numbers = re.findall(r'\d+', base)
                number_part = numbers[0] if numbers else '0'
            return int(number_part)
        except:
            return float('inf')  # Put files without numbers at the end
    
    return sorted(files, key=extract_number)


def extract_topic_from_json(file_path: Path) -> str:
    """Extract topic from JSON file."""
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            data = json.load(file)
            topic = data.get('topic', 'No topic found')
            return topic
    except FileNotFoundError:
        print(f"Error: The file {file_path} was not found.")
        return "Unknown Topic"
    except json.JSONDecodeError:
        print(f"Error: The file {file_path} contains invalid JSON.")
        return "Unknown Topic"
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return "Unknown Topic"


def extract_audio_from_json(file_path: Path) -> List[dict]:
    """Extract audio script from JSON file."""
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            data = json.load(file)
            audio_script = data.get('audio_script', [])
            return audio_script
    except FileNotFoundError:
        print(f"Error: The file {file_path} was not found.")
        return []
    except json.JSONDecodeError:
        print(f"Error: The file {file_path} contains invalid JSON.")
        return []
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return []


def json_extract(json_path: Path) -> List[str]:
    """Extract audio text from JSON file for subtitles."""
    audio_script = extract_audio_from_json(json_path)
    if audio_script:
        audio_data = []
        for item in audio_script:
            if 'text' in item:
                text = item['text']
                audio_data.append(text)
        return audio_data
    else:
        print("No audio script found in the JSON file.")
        return []


def add_effects(clip):
    """Add fade in and fade out effects to the video clip."""
    try:
        clip = clip.with_effects([vfx.FadeIn(duration=1)])
        clip = clip.with_effects([vfx.FadeOut(duration=1)])
        return clip
    except Exception as e:
        print(f"Error adding effects: {e}")
        return clip


def create_intro_clip(
    background_image_path: Path,
    duration: float,
    topic: str,
    font_path: Path
):
    """
    Create an intro video clip with a background image and centered text.
    
    Args:
        background_image_path: Path to the background image
        duration: Duration of the clip in seconds
        topic: The text to display
        font_path: Path to the TrueType font file
        
    Returns:
        VideoClip: A composite video clip with the background and centered text
    """
    try:
        check_file_exists(background_image_path)
        
        # Create background clip, fitted to the target (vertical) frame
        background = fit_to_frame(
            ImageClip(str(background_image_path)).with_duration(duration)
        )

        # Create text clip (wrapped to the frame width, large for mobile)
        text_clip = TextClip(
            text=topic,
            font_size=TITLE_FONT_SIZE,
            color='white',
            font=str(font_path),
            stroke_color='black',
            stroke_width=2,
            method='caption',
            size=(int(TARGET_W * 0.9), None),
            text_align='center',
        )

        # Set text position and duration
        text_clip = text_clip.with_position('center').with_duration(duration)

        # Composite video clip
        final_clip = CompositeVideoClip([background, text_clip], size=(TARGET_W, TARGET_H))

        return final_clip
    except Exception as e:
        print(f"Error creating intro clip: {e}")
        # Create a simple color clip as fallback
        from moviepy import ColorClip
        fallback_clip = ColorClip(size=(TARGET_W, TARGET_H), color=(0, 0, 0), duration=duration)
        text_clip = TextClip(
            text=topic, font_size=TITLE_FONT_SIZE, color='white', font=str(font_path),
            method='caption', size=(int(TARGET_W * 0.9), None), text_align='center',
        ).with_position('center').with_duration(duration)
        return CompositeVideoClip([fallback_clip, text_clip], size=(TARGET_W, TARGET_H))


def create_placeholder_image(
    width: int = TARGET_W,
    height: int = TARGET_H,
    text: str = "No Image",
    font_path: Optional[Path] = None,
    font_size: int = 50,
    text_color: str = "white",
    bg_color: str = "black"
) -> Path:
    """
    Creates a placeholder image with the specified dimensions and text.
    
    Args:
        width: Width of the image
        height: Height of the image
        text: Text to display on the image
        font_path: Path to the font file
        font_size: Size of the font
        text_color: Color of the text
        bg_color: Background color of the image
        
    Returns:
        Path to the generated placeholder image
    """
    try:
        img = Image.new("RGB", (width, height), bg_color)
        draw = ImageDraw.Draw(img)
        
        if font_path and font_path.exists():
            font = ImageFont.truetype(str(font_path), font_size)
        else:
            font = ImageFont.load_default()
        
        # Calculate text size
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        
        # Center the text
        text_position = ((width - text_width) // 2, (height - text_height) // 2)
        
        # Draw the text
        draw.text(text_position, text, font=font, fill=text_color)
        
        # Save placeholder in temp location
        placeholder_path = Path("placeholder_temp.png")
        img.save(placeholder_path)
        
        return placeholder_path
    except Exception as e:
        print(f"Error creating placeholder: {e}")
        # Return a simple path
        return Path("placeholder_temp.png")


def create_video(
    image_folder: Path,
    audio_folder: Path,
    script_path: Path,
    font_path: Path,
    output_file: Path,
    intro_image_path: Path,
    with_subtitles: bool = False,
    fps: int = 24,
    intro_duration: float = 2.0,
    outro_duration: float = 2.0,
) -> bool:
    """
    Main function that creates the video.
    
    Args:
        image_folder: Directory containing scene images
        audio_folder: Directory containing audio files
        script_path: Path to the script JSON file
        font_path: Path to the font file
        output_file: Path where the final video should be saved
        intro_image_path: Path to the intro background image
        with_subtitles: Whether to embed subtitles in the video
        fps: Frames per second for the output video
        
    Returns:
        True if successful, False otherwise
    """
    # Convert to Path objects
    image_folder = Path(image_folder) if image_folder else None
    audio_folder = Path(audio_folder)
    script_path = Path(script_path)
    font_path = Path(font_path)
    output_file = Path(output_file)
    intro_image_path = Path(intro_image_path)
    
    # Validate required paths
    check_folder_exists(audio_folder)
    check_file_exists(script_path)
    check_file_exists(font_path)
    check_file_exists(intro_image_path)
    
    # Ensure output directory exists
    output_file.parent.mkdir(parents=True, exist_ok=True)
    
    # Get audio files
    audio_files = get_files(audio_folder, ('.mp3', '.wav'))
    
    # Handle images
    if not image_folder or not image_folder.exists() or not list(image_folder.iterdir()):
        print("No images provided, creating placeholder")
        placeholder_image = create_placeholder_image(font_path=font_path, text="No Image Available")
        images = [placeholder_image] * len(audio_files)
    else:
        check_folder_exists(image_folder)
        images = get_files(image_folder, ('.jpg', '.png', '.jpeg'))
    
    # Extract subtitles from script
    subtitles = json_extract(script_path)
    raw_clips = []
    audio_durations = []
    
    # Create intro clip
    topic = extract_topic_from_json(script_path)
    intro_clip = create_intro_clip(intro_image_path, duration=intro_duration, topic=topic, font_path=font_path)
    raw_clips.append(intro_clip)
    
    # Create video clips with audio
    for img, audio in zip(images, audio_files):
        audio_clip = AudioFileClip(str(audio))
        image_clip = ImageClip(str(img)).with_duration(audio_clip.duration)
        # Fit each scene image to the vertical 9:16 frame (cover + center-crop)
        image_clip = fit_to_frame(image_clip).with_audio(audio_clip)
        audio_durations.append(audio_clip.duration)
        print(f"Video Clip no. {len(raw_clips)} successfully created")
        image_clip = add_effects(image_clip)
        raw_clips.append(image_clip)
    
    # Create outro clip
    outro_text = "MADE BY TEAM FLUX"
    outro_clip = create_intro_clip(intro_image_path, duration=outro_duration, topic=outro_text, font_path=font_path)
    raw_clips.append(outro_clip)
    
    # Concatenate all clips.
    #
    # "chain" rather than "compose": every clip here is already exactly
    # TARGET_W x TARGET_H (fit_to_frame covers-and-crops the scenes; the intro and
    # outro are composited at that size), and compose pays for a full
    # CompositeVideoClip layer on every frame to composite a single clip onto a
    # background that is never visible. Chain just plays them back to back. The
    # subtitle overlay below still needs a real composite — this only removes the
    # redundant inner one.
    video = concatenate_videoclips(raw_clips, method="chain")
    
    # Add subtitles if requested
    if with_subtitles:
        Start_duration = intro_duration
        subtitle_clips = []
        chunk_size = 10
        for text, duration in zip(subtitles, audio_durations):
            words = text.split()
            if len(words) > chunk_size:
                for i in range(0, len(words), chunk_size):
                    chunk = " ".join(words[i:i + chunk_size])
                    chunk_duration = duration * (len(chunk.split()) / len(words))
                    subtitle_clip = (
                        make_subtitle_clip(chunk, font_path)
                        .with_duration(chunk_duration)
                        .with_start(Start_duration)
                        .with_position(('center', SUBTITLE_Y), relative=True)
                    )
                    subtitle_clips.append(subtitle_clip)
                    Start_duration += chunk_duration
            else:
                subtitle_clip = (
                    make_subtitle_clip(text, font_path)
                    .with_duration(duration)
                    .with_start(Start_duration)
                    .with_position(('center', SUBTITLE_Y), relative=True)
                )
                subtitle_clips.append(subtitle_clip)
                Start_duration += duration
        subtitle_clips.insert(0, video)
        final_video = CompositeVideoClip(subtitle_clips, size=(TARGET_W, TARGET_H))
    else:
        final_video = video
    
    # Write video file. Use all CPU cores and the fastest preset for speed.
    # On a memory-constrained host (e.g. Render free), set FLUX_FFMPEG_THREADS=2.
    ffmpeg_threads = int(os.environ.get("FLUX_FFMPEG_THREADS", str(os.cpu_count() or 4)))
    ffmpeg_preset = os.environ.get("FLUX_FFMPEG_PRESET", "ultrafast")
    try:
        final_video.write_videofile(
            str(output_file),
            fps=fps,
            threads=ffmpeg_threads,
            preset=ffmpeg_preset,
            logger=None,
        )
        print(f"Video created successfully: {output_file}")
    finally:
        # Release decoders/encoders and audio handles to free memory promptly.
        for clip in raw_clips:
            try:
                clip.close()
            except Exception:
                pass
        try:
            final_video.close()
        except Exception:
            pass
    return True


def create_complete_srt(
    script_folder: Path,
    audio_file_folder: Path,
    outfile_path: Path,
    chunk_size: int = 10,
    intro_offset: float = 2.0,
) -> bool:
    """
    Creates a complete SRT file from script and audio files.
    
    Args:
        script_folder: Path to the script JSON file
        audio_file_folder: Directory containing audio files
        outfile_path: Path where the SRT file should be saved
        chunk_size: Number of words per subtitle chunk
        
    Returns:
        True if successful, False otherwise
    """
    # Convert to Path objects
    script_folder = Path(script_folder)
    audio_file_folder = Path(audio_file_folder)
    outfile_path = Path(outfile_path)
    
    # Ensure output directory exists
    outfile_path.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        # Extract script text
        script = json_extract(script_folder)
        audio_files = get_files(audio_file_folder, (".wav", ".mp3"))
        
        audio_clips = [AudioFileClip(str(x)) for x in audio_files]
        subs = pysrt.SubRipFile()
        start_time = intro_offset  # Account for intro
        n = 1
        
        for text, audio_clip in zip(script, audio_clips):
            duration = audio_clip.duration
            words = text.split()
            
            if len(words) > chunk_size:
                for i in range(0, len(words), chunk_size):
                    chunk = " ".join(words[i:min(i + chunk_size, len(words))])
                    chunk_duration = duration * (len(chunk.split()) / len(words))
                    end_time = start_time + chunk_duration
                    
                    subtitle = pysrt.SubRipItem(
                        index=n,
                        start=pysrt.SubRipTime(seconds=start_time),
                        end=pysrt.SubRipTime(seconds=end_time),
                        text=chunk
                    )
                    subs.append(subtitle)
                    start_time = end_time
                    n += 1
            else:
                chunk_duration = duration
                end_time = start_time + chunk_duration
                
                subtitle = pysrt.SubRipItem(
                    index=n,
                    start=pysrt.SubRipTime(seconds=start_time),
                    end=pysrt.SubRipTime(seconds=end_time),
                    text=text
                )
                subs.append(subtitle)
                start_time = end_time
                n += 1
        
        subs.save(str(outfile_path), encoding='utf-8')
        print(f"Subtitle file saved successfully at {outfile_path}")
        return True
        
    except Exception as e:
        print(f"Error creating SRT file: {e}")
        return False
