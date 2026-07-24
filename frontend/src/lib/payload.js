// Convert the creator form state into the backend VideoGenerationRequest shape.
export function buildVideoPayload({ topic, duration, keyPoints, autoPublish, privacy }) {
    const points = (keyPoints || '')
        .split(/\r?\n|,/)
        .map((line) => line.trim())
        .filter(Boolean);

    return {
        topic: topic.trim(),
        duration: Number(duration) || 60,
        key_points: points,
        style: 'educational',
        publish_to_youtube: Boolean(autoPublish),
        privacy_status: privacy === 'public' ? 'public' : 'unlisted',
    };
}

// Derive a human title from a generated video filename.
// e.g. "the_water_cycle_1717000000.mp4" -> "The Water Cycle"
export function titleFromFilename(filename) {
    if (!filename) return 'Untitled render';
    const base = filename.replace(/\.[^.]+$/, '');
    const withoutTimestamp = base.replace(/_\d{8,}$/, '');
    const words = withoutTimestamp.split(/[_\-\s]+/).filter(Boolean);
    if (!words.length) return 'Untitled render';
    return words
        .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
        .join(' ');
}
