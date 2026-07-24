# Flux — AI News-to-Video Automation

Flux monitors **Economic Times** RSS feeds, ranks the trending stories, and automatically turns the top ones into vertical (9:16) short-form videos — script, visuals, narration, subtitles, thumbnail — then publishes them to YouTube. Unattended, on a schedule.

News in → finished MP4 on your channel out, in about two minutes.

> Deep technical design lives in **[ARCHITECTURE.md](ARCHITECTURE.md)**.

---

## Features

- **Automated news pipeline** — scheduled ET RSS scan → heuristic ranking → render → publish
- **One-click automation** — pick how many top articles to process, toggle auto-publish, hit *Run Automation*
- **Live pipeline view** — the UI shows the **real** backend stage (Fetch News → Trend Analysis → Viral Script → Visuals → Narration → Subtitles → Assembly → Publish)
- **Pluggable script LLM** — **Gemini** (free tier, thinking disabled for speed) or **Ollama** (local, unlimited)
- **Visuals** — Pexels stock search (fetched in parallel) with Gemini image generation as fallback
- **Narration** — Kokoro TTS, fully local
- **Subtitles** — auto-timed captions burned in, plus a sidecar `.srt`; font scales with resolution so nothing clips
- **Duration-accurate** — output length tracks the requested duration via word-count targeting
- **Vertical output** — default **480×854 (9:16)** for fast Shorts renders; bump to 720p/1080p via env
- **YouTube publishing** — per-run or global, with **Unlisted / Public / Private** and `#Shorts` tagging
- **Library** — newest-first grid with thumbnails, duration badges, fullscreen player, download and delete

---

## How it works (short version)

```
cron tick ─▶ scan ET RSS ─▶ rank articles ─▶ take top N
                                                │
                dedup (skip already-processed) ◀┘
                                                ▼
              script → visuals → narration → subtitles → assemble
                        → thumbnail → publish to YouTube
                                                │
                          mark article as processed
```

### Ranking logic

Free and deterministic — no LLM cost. Each fresh article scores in `[0, 1]`:

```
score = 0.45 · recency  +  0.55 · trend
```

- **`recency`** — linear decay over `TRENDS_MAX_AGE_HOURS` (default 24h): `max(0, 1 − age / max_age)`. Just-published → 1.0, at/over max age → 0.0, unknown date → 0.5. Older articles are dropped.
- **`trend`** — cross-feed momentum. Titles + summaries are tokenized (stop-words and short tokens removed); for each keyword its **document frequency** `df` across the candidate set is computed. An article's raw trend is `Σ (df − 1)` over its unique keywords — it scores for every *other* article sharing its keywords — normalized by the batch maximum.

**Worked example** — an article published 6 hours ago (max age 24h) whose keywords are shared with several other articles in the batch:

```
recency = 1 − 6/24            = 0.75
trend   = 18 / 24 (batch max) = 0.75          # Σ(df−1) = 18, highest in batch = 24
score   = 0.45(0.75) + 0.55(0.75) = 0.75      # shown in the UI as 75
```

A fresher but isolated story (recency 0.95, trend 0.10) scores `0.45(0.95) + 0.55(0.10) = 0.48` — so **breadth of coverage outweighs raw freshness**, which is the intent: a story appearing across Markets, Economy and Industry is genuinely trending.

Net effect: **recent stories echoed across multiple ET sections rank highest.** Weights, stop-words and the tokenizer live at the top of [trends_service.py](backend/app/services/trends_service.py); the full derivation is in [ARCHITECTURE.md](ARCHITECTURE.md#ranking-algorithm).

---

## Tech stack

| Layer | Technology |
|---|---|
| Backend | FastAPI, Uvicorn, Pydantic, APScheduler |
| Script LLM | Google Gemini 2.5 Flash (`google-genai`) **or** Ollama (local) |
| Visuals | Pexels API → Gemini image generation fallback |
| Narration | Kokoro TTS (PyTorch, CPU) |
| Assembly | MoviePy + ffmpeg (`imageio-ffmpeg`), Pillow |
| Publishing | YouTube Data API v3 (`google-api-python-client`) |
| Frontend | React 19, Vite, Tailwind CSS |

---

## Setup

**Prerequisites:** Python **3.12** (ML deps lack wheels for 3.13+), Node 18+, a [Gemini API key](https://aistudio.google.com/app/apikey), and ideally a [Pexels key](https://www.pexels.com/api/).

### Backend
```bash
cd backend
py -3.12 -m venv venv                 # macOS/Linux: python3.12 -m venv venv
.\venv\Scripts\Activate.ps1           # macOS/Linux: source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env                  # fill in GEMINI_API_KEY (+ PEXELS_API_KEY)
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```
Health: http://127.0.0.1:8000/health · Docs: http://127.0.0.1:8000/docs

> Run **without `--reload`** for real renders — reload restarts on file changes and kills an in-progress render.

### Frontend
```bash
cd frontend
npm install
npm run dev                           # http://localhost:5173
```
The dev server proxies `/api` and `/static` to the backend.

### Optional: local script generation with Ollama
No API limits, no key:
```bash
ollama pull llama3.2
# backend/.env:  SCRIPT_PROVIDER=ollama
```

### Optional: YouTube publishing
1. Put an OAuth **desktop** client at `backend/secrets/youtube_client_secret.json`
2. `cd backend && python authorize_youtube.py` (opens a browser, writes `secrets/youtube_token.json`)
3. For deploys, paste the token JSON into `YOUTUBE_TOKEN_JSON`

> Set the OAuth consent screen to **"In production"**, otherwise Google revokes the refresh token every 7 days and you'll have to re-authorize weekly.

---

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | — | **Required** (script + image fallback) |
| `PEXELS_API_KEY` | — | Primary image source |
| `CORS_ORIGINS` | localhost dev | Allowed frontend origins |
| **Script** | | |
| `SCRIPT_PROVIDER` | `gemini` | `gemini` or `ollama` |
| `OLLAMA_MODEL` / `OLLAMA_BASE_URL` | `llama3.2` / `localhost:11434` | Ollama config |
| **Video** | | |
| `IMAGE_ASPECT_RATIO` | `1:1` | Use `9:16` for vertical Shorts |
| `FLUX_VIDEO_WIDTH` / `FLUX_VIDEO_HEIGHT` | `480` / `854` | Output resolution |
| `FLUX_FFMPEG_THREADS` | all cores | Lower (e.g. `2`) on small hosts |
| `INTRO_SECONDS` / `OUTRO_SECONDS` | `2` / `2` | Bookend lengths |
| **YouTube** | | |
| `YOUTUBE_TOKEN_JSON` | — | Token JSON for headless deploys |
| `YOUTUBE_AUTO_UPLOAD` | `false` | Publish every render regardless of request |
| `YOUTUBE_PRIVACY_STATUS` | `private` | `private` / `unlisted` / `public` |
| **Trending** | | |
| `TRENDS_ENABLED` | `false` | Run the scheduler |
| `TRENDS_AUTO_PUBLISH` | `false` | Auto-publish scheduled renders |
| `TRENDS_INTERVAL_HOURS` / `TRENDS_TOP_N` | `6` / `3` | Cadence / articles per run |
| **Frontend** | | |
| `VITE_API_BASE_URL` | — | Backend URL in production (build-time) |
| `VITE_DEMO_VIDEO_URL` | `/demo.mp4` | Hero demo clip |

Full list: [backend/.env.example](backend/.env.example).

---

## API

Base path `/api/v1`

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/videos/generate` | Start a render (`topic`, `duration`, `key_points`, `publish_to_youtube`, `privacy_status`) |
| `GET` | `/videos/status` | **Live render stage** (`fetch`…`publish`, `done`, `error`) |
| `GET` | `/videos/list` | Videos, newest first, with thumbnails |
| `GET` | `/videos/download/{name}` · `/videos/stream/{name}` | Download / stream |
| `DELETE` | `/videos/{name}` | Delete a video + thumbnail |
| `GET` | `/trends/status` · `/trends/preview` | Scheduler state / ranked articles |
| `POST` | `/trends/run?top_n=&auto_publish=` | Trigger one automation run |
| `GET` | `/health` | Health check |

---

## Performance

A 60-second video renders in roughly **130 s** on a typical laptop:

| Stage | Time |
|---|---|
| Script (Gemini, thinking disabled) | ~6 s |
| Visuals (parallel Pexels fetch) | ~3 s |
| Narration (Kokoro TTS, CPU) | ~46 s |
| Subtitles | <1 s |
| Assembly (ffmpeg encode) | ~69 s |

Narration and assembly dominate — they're real CPU work. See [ARCHITECTURE.md](ARCHITECTURE.md#performance) for why this can't be seconds without dropping the MP4 entirely.

---

## Deployment

Backend → **Render** ([render.yaml](render.yaml)) or **Railway** ([backend/Dockerfile](backend/Dockerfile)); frontend → **Vercel** ([frontend/vercel.json](frontend/vercel.json)). Guide: **[DEPLOYMENT.md](DEPLOYMENT.md)**.

> ⚠️ Rendering is RAM-heavy (torch + MoviePy + TTS). Render's free tier (512 MB) OOMs mid-render and its disk is ephemeral, so videos don't persist. Use a paid tier with a volume, or a host with ≥1 GB RAM.

---

## Notes & limitations

- **Python 3.12 only** — torch/Kokoro/spaCy lack wheels for newer versions.
- **One render at a time** — enforced by a lock; concurrent requests queue (renders share working directories).
- **Gemini free tier is rate-limited** (~20 req/min). Script generation uses a single call; switch to Ollama to avoid limits entirely.
- **Live status is in-memory** — a backend restart mid-render loses stage tracking (the UI detects this and stops cleanly).
- `backend/secrets/` and `.env` are gitignored — never commit OAuth files.
- The first render downloads the Kokoro and spaCy models once, then caches them.
