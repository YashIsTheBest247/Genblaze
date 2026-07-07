const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim();
const browserOrigin = typeof window !== 'undefined' ? window.location.origin : '';
const isViteDevServer = /:(5173|4173)$/.test(browserOrigin);
const fallbackBackendBaseUrl = 'http://127.0.0.1:8000';

// In dev (vite server), requests go through the proxy as same-origin paths.
// In prod, use the configured base URL or the page origin.
const baseUrl =
    configuredBaseUrl ||
    (browserOrigin.startsWith('http') && !isViteDevServer ? browserOrigin : '');

export const apiBaseUrl = baseUrl;
export const videosBaseUrl = `${baseUrl}/api/v1/videos`;

// Absolute URL for a backend-served media path (e.g. /static/videos/foo.mp4).
export function mediaUrl(path) {
    if (!path) return '';
    if (/^https?:\/\//.test(path)) return path;
    // When hitting the vite dev server directly (no proxy), point media at the backend.
    const mediaBase = baseUrl || (isViteDevServer ? fallbackBackendBaseUrl : '');
    return `${mediaBase}${path.startsWith('/') ? path : `/${path}`}`;
}

async function readResponse(response) {
    const contentType = response.headers.get('content-type') || '';
    return contentType.includes('application/json') ? response.json() : response.text();
}

export async function requestJson(url, options = {}) {
    const response = await fetch(url, options);
    const data = await readResponse(response);

    if (!response.ok) {
        const message = data?.detail || data?.error || data?.message || 'Request failed.';
        throw new Error(message);
    }

    return data;
}
