// Order must match the order the sections actually appear in App.jsx
// (Hero -> Automation -> Pipeline -> Library), otherwise the nav jumps
// backwards up the page.
export const navLinks = [
    { id: 'top', label: 'Dashboard' },
    { id: 'automation', label: 'Automation' },
    { id: 'pipeline', label: 'Pipeline' },
    { id: 'library', label: 'Library' },
];

export const pipelineSteps = [
    { key: 'fetch', label: 'Fetch News', statusText: 'Scanning Economic Times feeds', durationMs: 3000 },
    { key: 'trend', label: 'Trend Analysis', statusText: 'Scoring articles by recency + momentum', durationMs: 3000 },
    { key: 'script', label: 'Viral Script', statusText: 'Writing the scene-by-scene script', durationMs: 4000 },
    { key: 'image', label: 'Visuals', statusText: 'Sourcing images (Pexels → Gemini)', durationMs: 7000 },
    { key: 'voice', label: 'Narration', statusText: 'Generating narration audio', durationMs: 6000 },
    { key: 'subtitles', label: 'Subtitles', statusText: 'Timing subtitles to audio', durationMs: 5000 },
    { key: 'assembly', label: 'Assembly', statusText: 'Assembling the final cut', durationMs: 5000 },
    { key: 'provenance', label: 'Provenance', statusText: 'Signing the Genblaze manifest', durationMs: 2000 },
    { key: 'storage', label: 'Backblaze B2', statusText: 'Uploading artefacts to Backblaze B2', durationMs: 3000 },
    { key: 'publish', label: 'Publish', statusText: 'Publishing to YouTube', durationMs: 0 },
];
