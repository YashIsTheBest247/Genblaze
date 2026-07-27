# Flux — Provenance-Aware News-to-Video Automation

Flux monitors **Economic Times** RSS feeds, ranks the trending stories, and automatically turns the top ones into vertical (9:16) short-form videos — script, visuals, narration, subtitles, thumbnail. Every render is orchestrated and signed through the **[Genblaze](https://github.com/backblaze-labs/genblaze)** SDK and stored durably on **Backblaze B2**, then optionally published to YouTube. Unattended, on a schedule.

News in → a verifiable MP4 in object storage, in about two minutes.

**The problem it solves:** AI-generated news video is cheap to produce and impossible to audit. Flux makes every output answerable — the manifest travels with the file and says which model wrote the script, which model drew each frame, which voice narrated it, and what the SHA-256 of every artefact was at the moment it was made.

> Deep technical design lives in **[ARCHITECTURE.md](ARCHITECTURE.md)**. Deployment in **[DEPLOYMENT.md](DEPLOYMENT.md)**.

---

## Features

- **Automated news pipeline** — scheduled ET RSS scan → heuristic ranking → render → sign → store → publish
- **Provenance on every render** — a canonical, SHA-256-bound **Genblaze manifest** recording provider, model and parameters for each stage; written to B2 *and* embedded into the MP4 container, so the file carries its own record wherever it goes
- **Verifiable in the UI** — the library shows a verified shield per video; open **Provenance** to see the generation chain, per-artefact hashes and a live re-verification
- **Durable media library on Backblaze B2** — MP4, thumbnail, captions, script and manifest all land in the bucket; playback runs off short-lived presigned URLs, so the bucket stays private and the library survives an ephemeral host
- **Multi-provider generation via Genblaze** — visuals through Google Imagen or GMI Cloud, narration through GMI Cloud/ElevenLabs/MiniMax; swapping model or provider is one env var
- **Graceful degradation** — every generative stage has a fallback (Genblaze → Pexels → Gemini for visuals; cloud TTS → local Kokoro for narration), and the app boots and reports readiness even with nothing configured
- **One-click automation** — pick how many top articles to process, toggle auto-publish, hit *Run Automation*
- **Live pipeline view** — the UI shows the **real** backend stage (Fetch → Trend → Script → Visuals → Narration → Subtitles → Assembly → Provenance → Backblaze B2 → Publish)
- **Pluggable script LLM** — **Gemini** (free tier, thinking disabled for speed) or **Ollama** (local, unlimited)
- **Subtitles** — auto-timed captions burned in, plus a sidecar `.srt`; font scales with resolution so nothing clips
- **Duration-accurate** — output length tracks the requested duration via word-count targeting
- **Vertical output** — default **480×854 (9:16)** for fast Shorts renders; bump to 720p/1080p via env
- **YouTube publishing** — per-run or global, with **Unlisted / Public / Private** and `#Shorts` tagging
- **Single public URL** — the Docker image bundles the SPA into the API, so there is one host and no CORS

---

## How it works (short version)

```
cron tick ─▶ scan ET RSS ─▶ rank articles ─▶ take top N
                                                │
                dedup (skip already-processed) ◀┘
                                                ▼
              script → visuals → narration → subtitles → assemble → thumbnail
                                                │
                     Genblaze manifest (SHA-256 per artefact) ◀┘
                                                │
                      embed into MP4  +  upload to Backblaze B2
                                                │
                              publish to YouTube (optional)
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
| **Media orchestration + provenance** | **Genblaze** (`genblaze-core`, `genblaze-google`, `genblaze-gmicloud`) |
| **Durable storage** | **Backblaze B2** via `genblaze-s3` (S3-compatible, presigned URLs) |
| Script LLM | Google Gemini 2.5 Flash (`google-genai`) **or** Ollama (local) |
| Visuals | Genblaze (Imagen / GMI Cloud) → Pexels → Gemini fallback |
| Narration | Genblaze cloud TTS (GMI Cloud) **or** Kokoro TTS (PyTorch, CPU) |
| Assembly | MoviePy + ffmpeg (`imageio-ffmpeg`), Pillow |
| Publishing | YouTube Data API v3 (`google-api-python-client`) |
| Frontend | React 19, Vite, Tailwind CSS |

### How Genblaze and B2 are used

| Concern | Implementation |
|---|---|
| Generation | `Pipeline().step(provider, model=…, modality=…)` for visuals and narration — one step per scene/segment, 4-way concurrent, per-item fallback when a step yields nothing |
| Provenance | `Pipeline.ingest(assets, source=…, source_metadata={stages})` over the finished artefacts → a `Manifest` whose `canonical_hash` binds every asset's SHA-256 |
| Embedding | `Mp4Handler().embed()` writes the manifest into the MP4, verified by reading it straight back |
| Storage | `ObjectStorageSink(S3StorageBackend.for_backblaze(bucket), key_strategy=HIERARCHICAL)` for Genblaze runs; the same backend serves the library with presigned GETs |
| Verification | `Manifest.verify()` on read, surfaced as the shield badge and the Provenance panel |

Bucket layout:

```
flux/library/{video_stem}/video.mp4 · thumb.jpg · captions.srt
                          script.json · manifest.json · meta.json
flux/runs/{tenant}/{date}/{run_id}/manifest.json · assets/…     # Genblaze sink
```

---

## Setup

**Prerequisites:** Python **3.12** (ML deps lack wheels for 3.13+), Node 18+, a [Gemini API key](https://aistudio.google.com/app/apikey), a [Backblaze B2 bucket + application key](https://www.backblaze.com/sign-up/cloud-storage) (free tier: 10 GB), and ideally a [Pexels key](https://www.pexels.com/api/).

### Backblaze B2 (2 minutes)

1. **B2 Cloud Storage → Buckets → Create a Bucket**, set files to **Private**
2. **Application Keys → Add a New Application Key**, restricted to that bucket, **Read and Write**
3. Copy `keyID` → `B2_KEY_ID` and `applicationKey` → `B2_APP_KEY` (shown once)
4. The bucket's endpoint `s3.us-west-004.backblazeb2.com` gives `B2_REGION=us-west-004`

Flux runs without B2 — it just falls back to local disk and `/health` reports
`durable_storage: false`.

### Backend
```bash
cd backend
py -3.12 -m venv venv                 # macOS/Linux: python3.12 -m venv venv
.\venv\Scripts\Activate.ps1           # macOS/Linux: source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env                  # GEMINI_API_KEY + the four B2_* values
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```
Health: http://127.0.0.1:8000/health · Docs: http://127.0.0.1:8000/docs

`/health` reports exactly what is wired up:

```jsonc
{ "ready": true, "durable_storage": true,
  "checks": { "script_llm": true, "backblaze_b2": true,
              "genblaze": true, "genblaze_sink": true } }
```

### Tests

```bash
cd backend && venv/Scripts/python -m pytest tests/ -q
```

Covers the B2 upload/list/presign/delete path against a mocked S3 server, plus
manifest verification, tamper detection and the MP4 embed round-trip.

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
| `PEXELS_API_KEY` | — | Stock image source |
| `CORS_ORIGINS` | localhost dev | Allowed frontend origins (unused when the SPA is bundled) |
| **Backblaze B2** | | |
| `B2_KEY_ID` / `B2_APP_KEY` | — | **Required for durable storage** — application key pair |
| `B2_BUCKET` | — | Bucket name (keep it private) |
| `B2_REGION` | — | e.g. `us-west-004`, from the bucket endpoint |
| `B2_PREFIX` | `flux` | Key namespace inside the bucket |
| `B2_URL_TTL_SECONDS` | `3600` | Lifetime of presigned playback URLs |
| `KEEP_LOCAL_COPY` | `false` | Keep the MP4 on disk after upload |
| **Genblaze** | | |
| `GENBLAZE_ENABLED` | `true` | Master switch for orchestration + provenance |
| `GENBLAZE_TENANT_ID` | `flux` | Tenant stamped on every run/manifest |
| `GENBLAZE_KEY_STRATEGY` | `hierarchical` | `hierarchical` or `content_addressable` |
| `GENBLAZE_EMBED_MANIFEST` | `true` | Write the manifest into the MP4 |
| `GMI_API_KEY` | — | Optional — unlocks GMI Cloud image/audio models |
| `IMAGE_PROVIDER` | `auto` | `auto` (genblaze→pexels→gemini), `genblaze`, `pexels`, `gemini` |
| `GENBLAZE_IMAGE_MODEL` | `imagen-3.0-fast-generate-001` | `imagen-*` → Google, anything else → GMI Cloud |
| `TTS_PROVIDER` | `auto` | `auto`, `genblaze`, `kokoro` |
| `GENBLAZE_TTS_MODEL` | `minimax-tts-speech-2.6-turbo` | Cloud narration model |
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
| `GET` | `/videos/status` | **Live render stage** (`fetch`…`provenance`, `storage`, `publish`, `done`, `error`) |
| `GET` | `/videos/list` | Videos, newest first — presigned B2 URLs, storage + verification badges |
| `GET` | `/videos/storage` | **Backblaze B2 + Genblaze readiness** (bucket, sink, active providers) |
| `GET` | `/videos/{name}/provenance` | **Genblaze manifest**, re-verified on read: generation chain, per-artefact SHA-256 |
| `GET` | `/videos/download/{name}` · `/videos/stream/{name}` | Redirect to a presigned B2 URL (local file as fallback) |
| `DELETE` | `/videos/{name}` | Delete every artefact from B2 and local disk |
| `GET` | `/trends/status` · `/trends/preview` | Scheduler state / ranked articles |
| `POST` | `/trends/run?top_n=&auto_publish=` | Trigger one automation run |
| `GET` | `/health` | Health + readiness report |

The manifest is also readable without the app — it travels inside the MP4:

```bash
genblaze extract video.mp4     # print the embedded manifest
genblaze verify  video.mp4     # check it against the file's bytes
```

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

The root [Dockerfile](Dockerfile) builds the SPA and serves it from FastAPI, so a deploy is **one service, one public URL** — no CORS, nothing to wire together:

```bash
docker build -t flux .
docker run -p 8000:8000 --env-file backend/.env flux
```

Runs as-is on **Hugging Face Spaces** (free, 16 GB RAM — recommended), **Render** ([render.yaml](render.yaml)), **Railway**, **Fly.io** and **Cloud Run**. Full guide: **[DEPLOYMENT.md](DEPLOYMENT.md)**.

> Rendering is RAM-heavy (MoviePy + optional torch), so a 512 MB free tier will OOM — give it ≥1 GB. **Ephemeral disk is fine**: B2 owns the library, so restarts and redeploys don't lose anything. Setting `GMI_API_KEY` moves narration to the cloud and drops peak memory below 1 GB.

---

## Notes & limitations

- **Python 3.12 only** — torch/Kokoro/spaCy lack wheels for newer versions. (Cloud TTS via `GMI_API_KEY` removes torch from the critical path.)
- **One render at a time** — enforced by a lock; concurrent requests queue (renders share working directories).
- **Gemini free tier is rate-limited** (~20 req/min). Script generation uses a single call; switch to Ollama to avoid limits entirely.
- **Imagen needs a billed Google key.** On a free-tier key the Genblaze image step yields nothing, so Flux trips a one-shot circuit breaker and falls through to Pexels/Gemini for the rest of the process rather than paying that latency on every scene.
- **The manifest hashes the pre-embed bytes.** `GENBLAZE_EMBED_MANIFEST` rewrites the MP4 after the manifest is computed, so the recorded video SHA-256 describes the rendered content, not the annotated container. The sidecar `manifest.json` on B2 stays authoritative; `embed_manifest` verifies its own round-trip and reports false if the container rejected it.
- **Presigned URLs expire** after `B2_URL_TTL_SECONDS`. The library re-signs on every refresh; copied links go stale.
- **Live status is in-memory** — a backend restart mid-render loses stage tracking (the UI detects this and stops cleanly).
- `backend/secrets/` and `.env` are gitignored — never commit OAuth files.
- The first render downloads the Kokoro and spaCy models once, then caches them.
