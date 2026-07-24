import { requestJson, videosBaseUrl } from './client.js';

export async function generateVideo(payload) {
    return requestJson(`${videosBaseUrl}/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
}

export async function listVideos() {
    const response = await requestJson(`${videosBaseUrl}/list`);
    if (!Array.isArray(response)) {
        throw new Error('Video list response was not a valid array.');
    }
    return response;
}

// Live render status (which pipeline stage is running right now).
export async function getRenderStatus() {
    return requestJson(`${videosBaseUrl}/status`);
}

export async function deleteVideo(name) {
    return requestJson(`${videosBaseUrl}/${encodeURIComponent(name)}`, {
        method: 'DELETE',
    });
}
