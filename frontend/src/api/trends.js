import { apiBaseUrl, requestJson } from './client.js';

const trendsBaseUrl = `${apiBaseUrl}/api/v1/trends`;

// Fetch the ranked Economic Times trending articles (no generation).
export async function fetchTrending(topN = 9) {
    const response = await requestJson(`${trendsBaseUrl}/preview?top_n=${topN}`);
    return Array.isArray(response?.articles) ? response.articles : [];
}

// Trigger one automation run: generate the top N trending articles.
export async function runAutomation({ topN = 1, autoPublish = false } = {}) {
    return requestJson(
        `${trendsBaseUrl}/run?top_n=${topN}&auto_publish=${autoPublish ? 'true' : 'false'}`,
        { method: 'POST' }
    );
}
