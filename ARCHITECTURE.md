# Flux — Architecture

Technical design of the news-to-video automation system. For setup and usage see [README.md](README.md).

---

## 1. System overview

Flux is a two-tier application: a **FastAPI backend** that owns the render pipeline and a **React SPA** that drives and observes it.

```
┌──────────────────────────────────────────────────────────────────────┐
│ Frontend (React 19 + Vite + Tailwind)                                │
│                                                                      │
│  Hero ──▶ Automation ──▶ Pipeline (live) ──▶ Library                 │
│           (top-N, auto-publish, Run)   ▲          │                  │
└───────────────────┬────────────────────┼──────────┼──────────────────┘
                    │ POST /trends/run   │ GET      │ GET /videos/list
                    │ POST /videos/generate  /videos/status            │
                    ▼                    │          ▼
┌──────────────────────────────────────────────────────────────────────┐
│ Backend (FastAPI)                                                    │
│                                                                      │
│  ┌────────────────┐   ┌──────────────────────────────────────────┐   │
│  │ TrendsScheduler│──▶│ VideoGenerationService                    │   │
│  │ (APScheduler)  │   │  serialized by _render_lock               │   │
│  └───────┬────────┘   │                                          │   │
│          │            │  script → visuals → audio → subtitles →  │   │
│  ┌───────▼────────┐   │  assemble → thumbnail → publish          │   │
│  │ TrendsService  │   └──────────────────┬───────────────────────┘   │
│  │ fetch + rank   │                      │                           │
│  └───────┬────────┘                      ▼                           │
└──────────┼───────────────────────────────┼───────────────────────────┘
           │                               │
    Economic Times RSS            Gemini · Pexels · Kokoro
                                  ffmpeg · YouTube Data API
```

**Key property:** the backend is the single source of truth for render state. The frontend never simulates progress — it polls `/videos/status` and renders whatever stage the backend reports.

---

## 2. Backend components

| Module | Responsibility |
|---|---|
| [`app/main.py`](backend/app/main.py) | FastAPI app, CORS, static mount, lifespan (starts/stops the scheduler) |
| [`app/core/config.py`](backend/app/core/config.py) | Pydantic settings; all paths derived from `BASE_DIR` |
| [`app/api/v1/videos.py`](backend/app/api/v1/videos.py) | generate · status · list · download · stream · delete |
| [`app/api/v1/trends.py`](backend/app/api/v1/trends.py) | scheduler status · ranked preview · manual run |
| [`app/services/video_service.py`](backend/app/services/video_service.py) | The render pipeline + live status + render lock |
| [`app/services/trends_service.py`](backend/app/services/trends_service.py) | RSS fetch, normalization, ranking, dedup state |
| [`app/services/trends_scheduler.py`](backend/app/services/trends_scheduler.py) | APScheduler job; orchestrates rank → render → publish |
| [`app/services/youtube_service.py`](backend/app/services/youtube_service.py) | OAuth credential loading/refresh, resumable upload |
| [`imagegen/generate_script.py`](backend/imagegen/generate_script.py) | LLM script generation (Gemini or Ollama) |
| [`imagegen/gen_img.py`](backend/imagegen/gen_img.py) | Pexels search → Gemini image fallback (parallel) |
| [`tts/generate_audio_refactored.py`](backend/tts/generate_audio_refactored.py) | Kokoro TTS per narration segment |
| [`assembly/scripts/assembly_video_refactored.py`](backend/assembly/scripts/assembly_video_refactored.py) | MoviePy composition, captions, encode |

### Lazy loading of heavy dependencies

`torch`, `moviepy`, `kokoro` and `opencv` are **imported inside the render task**, not at module import:

```python
def _run_pipeline(self, request, video_filename):
    from imagegen.gen_img import main_generate_images
    from tts.generate_audio_refactored import main_generate_audio
    from assembly.scripts.assembly_video_refactored import create_video, create_complete_srt
```

This keeps the web process light at boot (~tens of MB instead of ~300 MB), so `/health`, `/trends/*` and `/videos/list` stay responsive on small instances and the container starts fast.

---

## 3. The render pipeline

Runs as a FastAPI background task, **serialized by a module-level lock**.

| # | Stage | Key | Implementation |
|---|---|---|---|
| 0 | Clean working dirs | — | wipes `resources/images`, `resources/audio` |
| 1 | Script | `script` | one LLM call → JSON (`audio_script` + `visual_script`) |
| 2 | Visuals | `image` | Pexels per scene, **4-way thread pool**, Gemini fallback |
| 3 | Narration | `voice` | Kokoro TTS → one `.wav` per segment |
| 4 | Subtitles | `subtitles` | timings derived from **actual audio durations** |
| 5 | Assembly | `assembly` | MoviePy: fit → concat → captions → ffmpeg encode |
| 6 | Thumbnail | — | ffmpeg frame grab → `<name>.jpg` |
| 7 | Publish | `publish` | YouTube resumable upload (optional) |

### Concurrency model

```python
_render_lock = threading.Lock()

def _generate_video_task(self, request, video_filename):
    with _render_lock:
        self._run_pipeline(request, video_filename)
```

Renders **share** working directories (`resources/images`, `resources/audio`, `resources/scripts/script.json`) and each run wipes them at the start. Two concurrent renders therefore delete each other's files mid-flight and both corrupt. The lock makes concurrent requests **queue** instead. The frontend additionally guards against double-submit.

### Live status protocol

A module-level dict is mutated as the pipeline advances and exposed over HTTP:

```python
render_status = {"active": bool, "stage": str|None, "topic": str,
                 "video_filename": str, "error": str|None, "updated_at": float}
```

Stage keys map 1:1 onto the frontend's `pipelineSteps`, so the UI highlights the real stage. `done` is set **after** publishing, which is what the client waits for before navigating to the library.

**Trade-off:** this is in-memory and single-process. A restart mid-render loses it. The frontend detects "backend idle but no new video" for 3 consecutive polls and stops cleanly rather than hanging.

### Duration accuracy

The finished video must match the requested duration:

```
narration_target = requested_duration − INTRO_SECONDS − OUTRO_SECONDS
target_words     = narration_target × WORDS_PER_SECOND      # ≈2.5 w/s
```

The word budget is injected into the LLM prompt as a hard constraint. Bookends default to 2 s each so they don't dominate short videos. Measured: a 15 s request produces a **14.55 s** file.

### Resolution-relative typography

All text sizes derive from the output width, so captions never clip when resolution changes:

```python
SUBTITLE_BOX_W     = int(TARGET_W * 0.90)
SUBTITLE_FONT_SIZE = max(14, int(TARGET_W * 0.055))
TITLE_FONT_SIZE    = max(18, int(TARGET_W * 0.075))
```

Captions use `method='caption'` with **auto height** (`size=(w, None)`) so long lines wrap downward instead of being cut off.

---

## 4. Trending subsystem

### Data flow

```
TRENDS_FEED_URLS ──▶ feedparser ──▶ normalize ──▶ dedup by link
                                                       │
                              filter stale (> MAX_AGE) ◀┘
                                                       │
                                    tokenize + score ──┴──▶ sort desc ──▶ top N
```

Articles are normalized to a dataclass (`title`, `link`, `summary`, `source`, `published_ts`) with HTML stripped.

### Ranking algorithm

```
score = W_RECENCY · recency + W_TREND · trend        # 0.45 / 0.55
```

**Recency** — linear decay, not exponential, so scores stay interpretable:

```
recency = max(0, 1 − age_seconds / (TRENDS_MAX_AGE_HOURS · 3600))
```
Unknown publish date → `0.5` (neutral). Articles beyond max age are removed from the candidate set entirely.

**Trend (cross-feed momentum)** — the core idea: *a story that appears across many feeds is trending, and its distinctive words will repeat across those articles.*

1. Tokenize `title + summary`: lowercase, `[a-zA-Z']{4,}`, minus a stop-word list (includes domain noise like `india`, `crore`, `lakh`).
2. Build document frequency `df[term]` = number of candidate articles containing that term.
3. Raw score per article: `Σ (df[t] − 1)` over its **unique** terms — it scores once for every *other* article sharing each term.
4. Normalize by the batch maximum → `[0, 1]`.

This is deliberately **not** TF-IDF. TF-IDF rewards *rare* terms; here we want the opposite — terms shared across the corpus signal a story with momentum.

**Properties**
- Deterministic and free (no LLM/API cost), so it can run every cycle.
- Self-normalizing: scores are relative to the current batch, so absolute feed volume doesn't matter.
- Weighted toward breadth (0.55) over freshness (0.45) — a story spanning Markets + Economy + Industry beats a brand-new isolated one.

### Deduplication

Processed article links persist in `resources/trends_state.json` (bounded to the last 500). Links are recorded even when a render *fails*, so one malformed article can't block the queue forever.

### Scheduling

`BackgroundScheduler` (thread-based, not asyncio) because the render is blocking CPU work — running it on the event loop would stall the API. Configured with `max_instances=1` and `coalesce=True`, so a long render can't stack up overlapping jobs.

---

## 5. Frontend architecture

State lives in `App.jsx`; sections are presentational.

| Component | Role |
|---|---|
| `Header` | nav + library search (lifted state) |
| `Hero` | pitch, demo video, pipeline stage strip |
| `AutomationSection` | top-N selector, auto-publish toggle, Run Automation, ranked articles |
| `PipelineSection` | live stage visualization |
| `LibrarySection` / `VideoCard` | newest-first grid, thumbnails, actions |
| `VideoPlayerModal` | fullscreen 9:16-safe player |
| `ConfirmDialog` | animated delete confirmation |

### The render watcher

A single interval (2.5 s) drives everything after a render starts:

```
poll ──▶ GET /videos/status ──▶ stage → highlight pipeline
     └─▶ GET /videos/list   ──▶ any name not in baseline? → new video
```

Completion requires the backend to report `done` (set *after* publishing) — not merely the file appearing, which happens one step earlier. A **baseline set** of filenames is captured before starting, so automation runs work even though the client can't know the filename in advance (the backend picks the article).

Termination conditions: `done` → success; `error` → show backend message; backend idle for 3 polls → "no video produced"; `maxAttempts` → timeout. On mount the app also **re-attaches** to an already-running render, so a page refresh doesn't lose the view.

---

## 6. Performance

Measured on a 12-core laptop, 60-second video, 480×854:

| Stage | Before | After | Change |
|---|---|---|---|
| Script | 23 s | **6 s** | `thinkingBudget: 0` on Gemini 2.5 Flash |
| Visuals | 14 s | **3 s** | sequential + `sleep(2)` → 4-way thread pool |
| Narration | 59 s | 46 s | — |
| Subtitles | 3 s | <1 s | — |
| Assembly | 94 s | 69 s | 480p, `ultrafast`, all cores |
| **Total** | **193 s** | **130 s** | **−33%** |

### Why it isn't seconds

Narration + assembly are ~88% of the remaining time, and both are irreducible **given the product requirement**: publishing to YouTube demands a real encoded MP4 file.

Systems that generate a "video" in 3–6 seconds (e.g. browser-composed reels) achieve it by **never encoding anything** — they play an image slideshow with on-device speech synthesis. That output cannot be uploaded to YouTube. The choice is therefore architectural, not an optimization gap:

| | Live-composed reel | Flux |
|---|---|---|
| Output | ephemeral browser playback | real `.mp4` |
| TTS | Web Speech API (0 s) | Kokoro (~46 s) |
| Encode | none | ffmpeg (~69 s) |
| Publishable to YouTube | ❌ | ✅ |

Remaining levers: drop fps 24→20 and remove per-clip fades (~15–20 s), or move TTS to a hosted API (trades local cost for network latency).

---

## 7. Design decisions

**One LLM call, not two.** Script generation was originally draft → segment (2 calls). Merged into a single call returning the final timestamped JSON — halves free-tier quota usage and latency. `response_mime_type=application/json` plus an explicit schema in the prompt keeps output parseable; a guard raises loudly if `audio_script` is missing rather than silently saving a broken script.

**Pexels before Gemini.** Stock search is a plain HTTP GET (fast, free, unlimited); image *generation* is slow and quota-limited. Gemini is a fallback only.

**Vertical 480p by default.** Encode time scales with pixel count; 480×854 is ~1/5 the pixels of 1080×1920 and still sharp on mobile, where Shorts are consumed. Resolution is env-configurable for quality-first runs.

**Env-var credentials for deploys.** `secrets/` is gitignored and cloud filesystems are ephemeral, so `YOUTUBE_TOKEN_JSON` can supply the token directly; token refresh writes back best-effort and tolerates read-only filesystems.

**Retries only where they help.** Transient LLM failures (`503`, `429`, `overloaded`) retry with backoff, honoring the server's `retry in Ns` hint. Render failures do not auto-retry — they'd burn quota repeating a deterministic problem.

---

## 8. Known limitations

- **Single-process state.** The render lock and live status are in-memory; horizontal scaling would need Redis or a task queue.
- **One render at a time.** Deliberate (shared working directories), but caps throughput.
- **Local library storage.** Videos live on the server filesystem; ephemeral hosts need a mounted volume.
- **Simulated stage granularity.** Progress is per-stage, not percentage-within-stage — MoviePy's encode progress isn't surfaced.
- **Python 3.12 pin.** torch/Kokoro/spaCy wheel availability.
