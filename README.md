# Flux — Provenance-Aware News-to-Video Automation

### ▶ **Live app: https://genblaze-production.up.railway.app**

[Health / readiness](https://genblaze-production.up.railway.app/health) ·
[API docs](https://genblaze-production.up.railway.app/docs) ·
[Storage + Genblaze status](https://genblaze-production.up.railway.app/api/v1/videos/storage)

One URL serves both the UI and the API — the Docker image bundles the React SPA
into FastAPI, so there is no separate frontend host and no CORS to configure.


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
- **Motion where it earns its keep** — the **opening** scenes use short **stock video clips**, because a viewer decides in the first second or two whether to keep watching. Clips are claimed by a sequential pass over the first scenes, so motion lands at the front rather than wherever a worker happened to finish. Each clip plays **once, at its natural length**, and the scene's own photograph holds the rest of the segment — looping a three-second clip across a twelve-second segment replayed it four times, which reads as a stutter and advertises that the shot is filler
- **Two stock libraries, ranked by relevance** — **Pexels and Unsplash** are searched concurrently and the result whose own caption best matches the prompt wins. Neither API ever returns *nothing* — each returns its loosest match silently — so scoring across two pools is what keeps scenes on topic. Gemini image generation covers what neither library has
- **Multi-provider generation via Genblaze** — narration through GMI Cloud/ElevenLabs/MiniMax, optional generative visuals; swapping model or provider is one env var
- **Graceful degradation** — every stage has a fallback (video clip → stock photo → Gemini for visuals; cloud TTS → local Kokoro for narration), and the app boots and reports readiness even with nothing configured
- **Fits the container it's given** — the render reads the cgroup memory limit and scales the number of video scenes to what the host can actually hold, because a render that gets OOM-killed produces nothing at all
- **Gemini key rotation** — the free tier caps generation at 20 requests *per day per project*; extra keys in `GEMINI_API_KEYS` are rotated to automatically when one reports its daily quota exhausted
- **Publishes on a fixed daily schedule** — 08:00, 14:00 and 20:00 Asia/Kolkata by default, with the last run recorded in B2 so a redeploy resumes the schedule instead of restarting it
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
08:00/14:00/20:00 IST ─▶ scan ET RSS ─▶ rank articles ─▶ take top N
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
| Visuals | Pexels **video** clips (opening scenes) → Pexels + Unsplash photo search, relevance-ranked → Gemini image generation (Genblaze generation opt-in) |
| Narration | Edge TTS (default — free, no key, no local model) · Genblaze cloud TTS · Kokoro (optional, local) |
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
flux/state/trends.json                                          # scheduler state
flux/runs/{tenant}/{date}/{run_id}/manifest.json · assets/…     # Genblaze sink
```

### How a scene picks its visual

Stock APIs never return *nothing*. Ask Pexels for `"Indian rupee banknotes close
up"` and it will hand back Turkish lira rather than admit it has no match — which
is exactly what it did, on a story about the rupee. So relevance is decided here,
not delegated:

```
1. video clip   Pexels video search, for the OPENING scenes only. Two tiers,
                tried per scene in order:
                  exact   — the clip's slug carries every identifying word of
                            the prompt (proper nouns included)
                  relaxed — at least one content word that is not mere framing;
                            "building" and "exterior" alone do not qualify
                Exact-then-relaxed *per scene*, not exact across all scenes
                first: a relaxed match on scene 0 beats an exact one on scene 3,
                because scene 3 is past the point where the viewer decided.
2. photo        Pexels and Unsplash searched concurrently; every candidate scored
                by how much of the prompt its own caption echoes; highest wins.
                Each provider retries with a shortened query, because Unsplash
                requires every term to match and returned zero for a six-word
                prompt every time.
3. generation   Gemini image generation, for what neither library has.
```

A scene that gets a clip **also** gets a photo: the clip plays once and the
photograph fills the remainder of the narration segment. Assembly therefore pairs
files by scene stem rather than listing them flat — listed flat, two files for
one scene would consume two narration segments and desync everything after them.

The relaxed tier can match the right subject in the wrong country: footage of the
New York Stock Exchange satisfies "stock" and "exchange" for a prompt about the
Bombay one. Those candidates are **ranked last rather than discarded**, so a
correct-place clip always wins when one exists and a wrong-place one is used only
when the alternative is no motion at all. `SCENE_VIDEO_STRICT_PLACE=true` drops
them instead, at the cost of putting a still back on the opening scene.

Two details worth knowing. Searches use the **output frame's orientation**, not a
configured one — scene images are cover-cropped to the video anyway, so searching
square for a vertical video only shrinks the pool and crops more away. And photo
IDs are remembered across renders in `resources/recent_photo_ids.json`, so two
consecutive videos on the same subject don't ship the same frame.

Video scenes are bounded by memory rather than preference. Each open clip holds
an ffmpeg decoder for the whole write — measured at ~137 MB on a ~385 MB
baseline — so the count is derived from the container's cgroup limit:

| Container | Video scenes | Peak |
|---|---|---|
| 512 MB | 0 (stills only) | 383 MB (measured) |
| 1 GB (≈953 MB usable) | 2 | 654 MB (measured) |
| 2 GB and up | 4 | ~950 MB (projected) |

Three clips measures 798 MB — it fits a 1 GB container, but at 84% of the limit,
and a killed render produces nothing at all. Hence two.
`SCENE_VIDEO_MEMORY_GATE=false` uses `SCENE_VIDEO_MAX_CLIPS` verbatim and skips
the check, if you would rather take that risk.

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
| `GEMINI_API_KEYS` | — | Extra keys, comma-separated. Rotated to when one hits its **daily** quota; must be from different Google Cloud projects to add anything |
| `PEXELS_API_KEY` | — | Stock photos and video clips |
| `UNSPLASH_ACCESS_KEY` | — | Optional second photo library, searched alongside Pexels |
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
| `GENBLAZE_SINK_ASSETS` | `false` | Also push every artefact through the Genblaze sink. Off by default — the storage stage already uploads them, so this duplicated ~15 s of upload per render |
| `GMI_API_KEY` | — | Optional — unlocks GMI Cloud image/audio models |
| `IMAGE_PROVIDER` | `auto` | `auto` (pexels→gemini), `genblaze` (genblaze→pexels→gemini), `pexels`, `gemini` |
| `GENBLAZE_IMAGE_MODEL` | `gemini-2.5-flash-image` | `gemini-*-image` → Gemini `generateContent`, `imagen-*` → Imagen, else → GMI Cloud |
| `TTS_PROVIDER` | `auto` | `auto` (genblaze→edge→kokoro), `edge`, `genblaze`, `kokoro` |
| `EDGE_TTS_VOICE_MALE` / `_FEMALE` | `en-GB-RyanNeural` / `en-GB-SoniaNeural` | `edge-tts --list-voices` for all 47 |
| `GENBLAZE_TTS_MODEL` | `minimax-tts-speech-2.6-turbo` | Cloud narration model |
| **Script** | | |
| `SCRIPT_PROVIDER` | `gemini` | `gemini` or `ollama` |
| `OLLAMA_MODEL` / `OLLAMA_BASE_URL` | `llama3.2` / `localhost:11434` | Ollama config |
| **Video** | | |
| `IMAGE_ASPECT_RATIO` | `9:16` | Shape to search for. A value whose orientation contradicts the output frame is overridden at runtime — searching square for a vertical video draws from a tiny, badly-matched pool |
| `FLUX_VIDEO_WIDTH` / `FLUX_VIDEO_HEIGHT` | `480` / `854` | Output resolution |
| `FLUX_FFMPEG_THREADS` | all cores | Lower (e.g. `2`) on small hosts |
| `SCENE_VIDEO_CLIPS` | `true` | Use stock video clips for the opening scenes |
| `SCENE_VIDEO_MAX_CLIPS` | `4` | Ceiling on video scenes — lowered automatically to what the container's memory can hold |
| `SCENE_VIDEO_MEMORY_GATE` | `true` | Set `false` to use the cap verbatim and skip the memory check |
| `SCENE_VIDEO_MIN_HEIGHT` | `854` | Smallest clip rendition to download (masters are 4K) |
| `SCENE_VIDEO_STRICT_PLACE` | `false` | `true` drops opening clips naming another country instead of ranking them last — cleaner, but forfeits the slot and puts a still on scene 0 |
| `INTRO_SECONDS` / `OUTRO_SECONDS` | `2` / `2` | Bookend lengths |
| **YouTube** | | |
| `YOUTUBE_TOKEN_JSON` | — | Token JSON for headless deploys |
| `YOUTUBE_AUTO_UPLOAD` | `false` | Publish every render regardless of request |
| `YOUTUBE_PRIVACY_STATUS` | `private` | `private` / `unlisted` / `public` |
| **Trending** | | |
| `TRENDS_ENABLED` | `false` | Run the scheduler |
| `TRENDS_AUTO_PUBLISH` | `false` | Auto-publish scheduled renders (`YOUTUBE_AUTO_UPLOAD` also suffices) |
| `TRENDS_SCHEDULE_HOURS` | `8,14,20` | Publish times, 24-hour clock |
| `TRENDS_TIMEZONE` | `Asia/Kolkata` | Timezone those times are read in |
| `TRENDS_TOP_N` | `1` | Videos per slot — 1 × 3 slots = 3 a day |
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

A short renders in roughly **35 s** (measured, 20 s video, 2 scenes); a 60 s
video with seven scenes, three of them video, takes about **120 s** — each clip
is transcoded to the output frame once before assembly, which is what keeps peak
memory flat as the video gets longer.

| Stage | Time | Note |
|---|---|---|
| Script (Gemini) | ~9 s | one LLM call for draft *and* segmentation |
| Visuals | ~3 s | video + photo searches, 4-way thread pool |
| **Narration (Edge TTS)** | **~2 s** | was ~46 s on local Kokoro |
| Subtitles | <1 s | timed from real audio durations |
| Assembly (ffmpeg encode) | ~14 s | dominates |
| Provenance | ~0.3 s | manifest + embed |
| B2 upload | ~6 s | six objects, uploaded concurrently |

Down from ~55 s, by removing work rather than tuning it:

- **Provenance was uploading everything to B2 twice** (15.4 s → 0.3 s). The
  storage stage already publishes every artefact *and* the manifest, so handing
  the same files to the Genblaze sink re-sent every byte. `GENBLAZE_SINK_ASSETS`
  now defaults off; the manifest is still built, verified and embedded.
- **Assembly composited every frame onto an invisible background** (20.7 s →
  14 s). `concatenate_videoclips(method="compose")` builds a full
  `CompositeVideoClip` per frame; every clip is already exactly 480×854, so
  `method="chain"` does the same job. Frame rate also dropped 24 → 20, which is
  linear in encode time and invisible on stills with crossfades.
- **The six B2 uploads ran serially** and are now concurrent, bounded by the
  slowest one instead of their sum.

Assembly still dominates, and it's irreducible: publishing demands a real encoded
MP4. See [ARCHITECTURE.md](ARCHITECTURE.md#performance).

---

## Deployment

Deployed on **Railway** at
[genblaze-production.up.railway.app](https://genblaze-production.up.railway.app).
The root [Dockerfile](Dockerfile) builds the SPA and serves it from FastAPI, so a
deploy is **one service, one public URL** — no CORS, nothing to wire together:

```bash
docker build -t flux .
docker run -p 8000:8000 --env-file backend/.env flux
```

The same image runs on **Railway**, **Render** ([render.yaml](render.yaml)),
**Fly.io** and any Docker host. Full guide: **[DEPLOYMENT.md](DEPLOYMENT.md)**.

> **Ephemeral disk is fine** — B2 owns the library, so restarts and redeploys
> lose nothing, and the scheduler's own state lives there too.
>
> **Memory is the real constraint.** With narration off-device the image carries
> no torch, so a stills-only render peaks near 380 MB instead of 1.5 GB. Video
> scenes add ~137 MB each, and the app derives how many it can afford from the
> container's cgroup limit rather than guessing — a 1 GB host runs two, a 512 MB
> host runs none and uses photographs. Renders were being OOM-killed before that
> gate existed, and a killed render produces nothing at all.

---

## Notes & limitations

- **Narration is a network call by default.** Edge TTS is an unofficial client for Microsoft's Edge read-aloud endpoint — widely used and stable, but not a contractual API. `TTS_PROVIDER=kokoro` (plus `requirements-kokoro.txt`) restores fully-offline narration if you need it.
- **The default image has no torch.** That's deliberate: with Kokoro in the container, the ~350 MB model download happened during the first render, concurrent with the torch load, and the memory spike OOM-killed the process on every deploy. Removing it cut ~600 MB of dependencies and halved render time.
- **Python 3.12 only** — the optional Kokoro extras lack wheels for newer versions.
- **One render at a time** — enforced by a lock; concurrent requests queue (renders share working directories).
- **The Gemini limit that actually bites is per *day*, not per minute** — 20 `generate_content` requests per project per day on the free tier. A render spends one, so a single key is good for ~20 videos a day. Extra keys in `GEMINI_API_KEYS` are rotated to when one reports its daily quota exhausted; they must come from **different Google Cloud projects**, since keys in the same project share one quota. No amount of backoff clears a daily cap, so that error rotates rather than retries. `SCRIPT_PROVIDER=ollama` avoids limits entirely.
- **Some Gemini models reject `thinking_budget=0`.** Disabling "thinking" is the single biggest latency win on Flash, but the 3.x line (which `gemini-flash-latest` aliases) refuses it with a bare 400 naming no field. The generator discovers this on the first call and stops asking.
- **Scheduled slots are wall-clock, and a missed one is caught up.** An interval trigger fires a full interval *after process start*, so on a host that redeploys often it can come due never — which is exactly what happened, silently, while the scheduler reported itself healthy on every boot. The last completed run is recorded in B2 (`flux/state/trends.json`), so a redeploy resumes the schedule; a slot the container slept through runs shortly after boot, within a 3-hour grace window.
- **Google image generation needs billing — verified, not assumed.** Every `imagen-*` model now returns *"no longer available to new users"* on the Gemini API, and `gemini-*-image` reports `generate_content_free_tier_requests, limit: 0`. So on a free Gemini key Genblaze cannot produce visuals at all, and Flux trips a one-shot circuit breaker and serves scenes from Pexels for the rest of the process rather than paying that latency on every scene. Enable billing on the Google key, or add a `GMI_API_KEY`, to get Genblaze-generated visuals.
- **Genblaze's Google image adapter is Imagen-only**, and Imagen is closed. [`genblaze_gemini_image.py`](backend/app/services/genblaze_gemini_image.py) adds a `SyncProvider` for the `generateContent` image models so the Genblaze path stays usable on a modern Gemini key.
- **Genblaze refuses `file://` reads outside the system temp directory** (an arbitrary-file-read guard, with no override plumbed through the sink). Render artefacts live under `static/` and `resources/`, so both the ingest and the image pipeline stage copies in temp. Worth knowing before you add a fourth artefact type.
- **The manifest hashes the pre-embed bytes.** `GENBLAZE_EMBED_MANIFEST` rewrites the MP4 after the manifest is computed, so the recorded video SHA-256 describes the rendered content, not the annotated container. The sidecar `manifest.json` on B2 stays authoritative; `embed_manifest` verifies its own round-trip and reports false if the container rejected it.
- **Presigned URLs expire** after `B2_URL_TTL_SECONDS`. The library re-signs on every refresh; copied links go stale.
- **Live status is in-memory** — a backend restart mid-render loses stage tracking (the UI detects this and stops cleanly).
- `backend/secrets/` and `.env` are gitignored — never commit OAuth files.
- The first render downloads the Kokoro and spaCy models once, then caches them.
