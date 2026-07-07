# Flux — AI Video & Podcast Automation

Flux turns a topic into a finished, vertical (9:16) short-form **video** or **podcast**, then auto-publishes it to YouTube. It can also run unattended: monitoring **Economic Times** RSS feeds, ranking trending stories, and generating videos for the top picks on a schedule.

Topic in → script, visuals, narration, subtitles, assembled MP4, thumbnail, and a YouTube upload out.

---

## Features

- **Two output modes**
  - **Video** — a distinct image per scene (Pexels stock → Gemini image fallback)
  - **Podcast** — a single themed cover image held over the full narration
- **Pluggable script generation** — **Gemini** (free tier) or **Ollama** (local, free, unlimited)
- **Stock + generated visuals** — Pexels primary, Gemini image generation fallback
- **Text-to-speech** — narration via Kokoro TTS (local)
- **Subtitles** — auto-timed captions burned into the video (+ a sidecar `.srt`)
- **Duration-accurate** — output length tracks the chosen duration (word-count targeting + short intro/outro)
- **Vertical assembly** — default **480×854 (9:16)** for fast Shorts renders; bump to 720/1080 via env
- **Thumbnails** — a poster frame is generated per render and shown in the library
- **YouTube auto-publishing** — per render or globally, with **Unlisted / Public / Private** visibility and `#Shorts` tagging
- **Trending pipeline** — scheduled ET RSS monitoring, heuristic ranking, auto-generation of the top N
- **Trending UI** — a live, animated section to browse ranked headlines and one-click "Generate video"
- **Web UI** — studio to create renders, watch the live pipeline, browse/delete the library (animated confirm modal)

---

## Architecture

```
                          ┌──────────────────────────────┐
   Browser  ──────────▶   │  Frontend (React + Vite)      │
                          │  studio · trending · pipeline │
                          │  · library                    │
                          └───────────────┬───────────────┘
                                           │  /api/v1  (Vite proxy in dev)
                                           ▼
                          ┌──────────────────────────────┐
                          │  Backend (FastAPI)            │
                          │                               │
   ET RSS feeds ─▶ Trends │  VideoGenerationService       │ ─▶ YouTube
   (APScheduler)  service │   1. script  (Gemini|Ollama)  │   (Data API)
                          │   2. images  (Pexels→Gemini)  │
                          │      └─ podcast: single cover │
                          │   3. audio   (Kokoro TTS)     │
                          │   4. subtitles                │
                          │   5. assemble (MoviePy 9:16)  │
                          │   6. thumbnail (ffmpeg)       │
                          │   7. publish (optional)       │
                          └──────────────────────────────┘
```

### Backend pipeline ([backend/app/services/video_service.py](backend/app/services/video_service.py))
A render runs as a background task, **serialized by a lock** (one render at a time — shared working dirs):
1. **Script** — `imagegen/generate_script.py` — a single LLM call (Gemini or Ollama) returns JSON with `audio_script` + `visual_script`, length-targeted to the chosen duration
2. **Visuals** — `imagegen/gen_img.py` (Pexels → Gemini fallback). Podcast mode sources one cover.
3. **Audio** — `tts/generate_audio_refactored.py` (Kokoro)
4. **Subtitles** — `assembly/scripts/assembly_video_refactored.py`
5. **Assembly** — MoviePy composes images + audio + captions into a vertical MP4
6. **Thumbnail** — poster frame via ffmpeg
7. **Publish** — optional YouTube upload (`app/services/youtube_service.py`)

> Heavy ML deps (torch, MoviePy, Kokoro) are **lazy-imported** inside the render task, so the API/health/trends endpoints stay light at boot.

### Frontend ([frontend/](frontend/))
React 19 + Vite + Tailwind. Dev proxies `/api` and `/static` to the backend; prod uses `VITE_API_BASE_URL`.

---

## Trending pipeline (Economic Times → auto-published video)

An **APScheduler cron job** runs every `TRENDS_INTERVAL_HOURS` (default 6h) and does the following, unattended ([trends_scheduler.py](backend/app/services/trends_scheduler.py), [trends_service.py](backend/app/services/trends_service.py)):

```
   cron tick ─▶ scan ET RSS feeds ─▶ rank articles ─▶ take top N
                                                        │
                     dedup (skip already-processed) ◀───┤
                                                        ▼
                         for each: script → images → audio → subtitles
                                   → assemble → thumbnail → AUTO-PUBLISH (YouTube)
                                                        │
                                   mark article link as processed
```

1. **Scan** — fetches every feed in `TRENDS_FEED_URLS` with `feedparser`, normalizes each entry (title, summary, link, source, published time), and de-duplicates by link.
2. **Rank** — scores each fresh article (see below) and sorts descending.
3. **Select** — takes the top `TRENDS_TOP_N` articles, skipping any whose link is already in `resources/trends_state.json`.
4. **Generate** — runs each through the normal render pipeline (serialized, one at a time), using the headline as the topic and the article's salient keywords as key points.
5. **Publish** — if `TRENDS_AUTO_PUBLISH=true`, uploads each finished video to YouTube.
6. **Dedup** — records processed links so the same story is never regenerated.

It's **off by default** — set `TRENDS_ENABLED=true` to start the scheduler. You can also preview the ranking (`GET /trends/preview`) or trigger a run on demand (`POST /trends/run`).

### Ranking logic

Free, deterministic — no LLM/API cost. Each candidate article gets a score in `[0, 1]`:

```
score = 0.45 · recency  +  0.55 · trend
```

- **`recency`** — how fresh the article is, linearly decayed over `TRENDS_MAX_AGE_HOURS` (default 24h):
  `recency = max(0, 1 − age / max_age)`. Just-published → 1.0; at/over the max age → 0.0; unknown publish date → neutral 0.5. Articles older than `max_age` are dropped entirely.

- **`trend`** — cross-feed momentum: a story surfacing across *many* articles/feeds is "trending". Each article's title + summary is tokenized (lowercased, stop-words and short tokens removed). For every unique keyword, its **document frequency** `df` (how many candidate articles contain it) is computed. An article's raw trend is `Σ (df − 1)` over its unique keywords — i.e. it scores for every *other* article that shares its keywords. This is then normalized to `[0, 1]` by the highest raw value in the batch.

The weights (`0.45` / `0.55`) and the tokenizer/stop-words live at the top of [trends_service.py](backend/app/services/trends_service.py). The result is that **recent stories echoed across multiple ET sections rank highest** — exactly the ones worth turning into a short.

---

## Tech stack

| Layer | Tech |
|---|---|
| Backend | FastAPI, Uvicorn, Pydantic |
| Script LLM | Google Gemini (`google-genai`) **or** Ollama (local) |
| Media | Pexels API, Kokoro TTS (torch), MoviePy + ffmpeg, Pillow, OpenCV |
| Scheduling | APScheduler, feedparser |
| Publishing | YouTube Data API (`google-api-python-client`, `google-auth`) |
| Frontend | React 19, Vite, Tailwind CSS |

---

## Setup

**Prerequisites:** Python **3.12** (ML deps lack wheels for 3.13+), Node 18+, a [Gemini API key](https://aistudio.google.com/app/apikey) (or Ollama), and ideally a [Pexels key](https://www.pexels.com/api/).

### Backend
```bash
cd backend
py -3.12 -m venv venv          # macOS/Linux: python3.12 -m venv venv
.\venv\Scripts\Activate.ps1    # macOS/Linux: source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env           # fill in GEMINI_API_KEY (and PEXELS_API_KEY)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
Health: http://127.0.0.1:8000/health · Docs: http://127.0.0.1:8000/docs

> Run **without `--reload`** for real renders — `--reload` restarts (and kills an in-progress render) when watched files change.

### Frontend
```bash
cd frontend
npm install
npm run dev                     # http://localhost:5173
```

### Script via Ollama (optional — free & unlimited, no API limits)
1. Install Ollama: https://ollama.com/download
2. `ollama pull llama3.2`  (or `llama3.1` for higher quality)
3. In `backend/.env`: `SCRIPT_PROVIDER=ollama` (and `OLLAMA_MODEL=llama3.2`)
4. Restart the backend — scripts now generate locally, using zero Gemini quota.

### YouTube publishing (optional)
1. Put your OAuth **desktop** client at `backend/secrets/youtube_client_secret.json`.
2. `cd backend && python authorize_youtube.py` (opens a browser, writes `secrets/youtube_token.json`).
3. For headless/deploys, paste the token JSON into `YOUTUBE_TOKEN_JSON`.

> Set the OAuth consent screen to **"In production"** so refresh tokens don't expire after 7 days.

---

## Configuration (key environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | — | **Required.** Script (if provider=gemini) + image fallback |
| `PEXELS_API_KEY` | — | Primary image source |
| `CORS_ORIGINS` | localhost dev | Allowed frontend origins (no trailing slash) |
| **Script provider** | | |
| `SCRIPT_PROVIDER` | `gemini` | `gemini` or `ollama` |
| `OLLAMA_MODEL` / `OLLAMA_BASE_URL` | `llama3.2` / `localhost:11434` | Ollama config |
| **Video** | | |
| `IMAGE_ASPECT_RATIO` | `1:1` | Use `9:16` for vertical Shorts images |
| `FLUX_VIDEO_WIDTH` / `FLUX_VIDEO_HEIGHT` | `480` / `854` | Output resolution (720×1280 / 1080×1920 for higher quality) |
| `FLUX_FFMPEG_THREADS` | all cores | Lower (e.g. `2`) on memory-constrained hosts |
| `INTRO_SECONDS` / `OUTRO_SECONDS` | `2` / `2` | Bookend lengths |
| **YouTube** | | |
| `YOUTUBE_TOKEN_JSON` | — | Token JSON for headless deploys |
| `YOUTUBE_AUTO_UPLOAD` | `false` | Publish every render (else the UI toggle decides) |
| `YOUTUBE_PRIVACY_STATUS` | `private` | `private` / `unlisted` / `public` |
| **Trending** | | |
| `TRENDS_ENABLED` / `TRENDS_AUTO_PUBLISH` | `false` | Run scheduler / auto-publish |
| `TRENDS_INTERVAL_HOURS` / `TRENDS_TOP_N` | `6` / `3` | Cadence / stories per run |
| **Frontend (Vercel)** | | |
| `VITE_API_BASE_URL` | — | Backend URL in production (build-time) |

See [backend/.env.example](backend/.env.example) for the full list.

---

## API

Base path: `/api/v1`

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/videos/generate` | Start a render (`topic`, `duration`, `key_points`, `style`, `publish_to_youtube`, `privacy_status`) |
| `GET` | `/videos/list` | List finished videos (+ thumbnails) |
| `GET` | `/videos/download/{name}` · `/videos/stream/{name}` | Download / stream an MP4 |
| `DELETE` | `/videos/{name}` | Delete a video + its thumbnail |
| `GET` | `/trends/status` · `/trends/preview` | Scheduler status / ranked trending articles |
| `POST` | `/trends/run` | Trigger one trending run now |
| `GET` | `/health` | Health check |

---

## Deployment

Backend → **Render** ([render.yaml](render.yaml)), frontend → **Vercel** ([frontend/vercel.json](frontend/vercel.json)). Full guide in **[DEPLOYMENT.md](DEPLOYMENT.md)**.

> ⚠️ Rendering is RAM-heavy (torch + MoviePy + TTS). Render's free tier (512 MB) OOMs **during a render** and its disk is ephemeral, so generated videos don't persist. Reliable rendering + persistence needs the **Standard** plan (2 GB + disk), or host the backend elsewhere. To show videos on the free deployment, commit them under `backend/static/videos/` so they ship with the build.

---

## Notes & limitations

- **Python 3.12 only** — torch/Kokoro/spaCy lack wheels for newer versions.
- **One render at a time** — enforced by a lock; concurrent requests queue.
- **Gemini free tier is rate-limited** (~20 req/min) — script generation now uses a single call; switch to Ollama to avoid limits entirely.
- **Secrets** — `backend/secrets/` and `.env` are gitignored; never commit OAuth files.
- First render downloads the Kokoro + spaCy models once (cached afterward).
