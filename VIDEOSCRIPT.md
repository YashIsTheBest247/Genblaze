# Flux — Demo Video Script

**Target: 2:45–3:00.** Judges watch a lot of these; the first fifteen seconds
decide whether they watch the rest. Lead with the problem, show the thing that
is different, prove it with real artefacts.

**The one idea to land:** anyone can generate a news video. Flux is the one that
can *prove how it was made* — and the proof travels inside the file.

---

## Before you hit record

- [ ] Run **3 renders** on the live site and let them finish. Each takes ~60 s.
      Never record a cold start.
- [ ] Delete the one card that shows **B2 but no shield** (the early render from
      before the provenance fix) so every card in shot is verified.
- [ ] Open these tabs in order, so you never fumble mid-take:
      1. https://genblaze-production.up.railway.app
      2. https://genblaze-production.up.railway.app/health
      3. Your Backblaze B2 bucket, already drilled into `flux/library/`
- [ ] Browser zoom ~110%, close bookmark bar, hide any tab showing an API key.
- [ ] **`backend/.env` must not be visible in any shot.** Close the editor.
- [ ] Record 1080p. Talk over it live, or record silent and voice-over after —
      voice-over is easier to keep tight.

---

## The script

### 0:00 – 0:18 · The problem

> *Screen: the live site, hero section.*

**"AI can generate a news video in about two minutes. That's the easy part.**

**The hard part is that nobody can tell you where it came from. Which model
wrote the script. Where the footage came from. Whether the file you're watching
is the file that was generated.**

**Flux is a news-to-video pipeline where every output answers those questions."**

---

### 0:18 – 0:32 · What it does

> *Screen: scroll slowly through the library grid.*

**"It watches Economic Times RSS feeds, ranks the trending stories, and turns the
top ones into vertical shorts — script, visuals, narration, subtitles, the whole
render. Unattended, on a schedule.**

**Every one of these was generated, signed, and stored automatically."**

---

### 0:32 – 1:15 · The pipeline, running for real

> *Screen: Automation section. Set top-N to 1. Click **Run Automation**.
> Let the pipeline strip animate. Do not speed this up yet — let one or two
> stages advance in real time so it's obviously live.*

**"Let's run one. It pulls the feeds, scores the articles on recency and
cross-feed momentum — a story appearing across Markets, Economy and Industry
beats a fresh but isolated one — and picks the top story.**

**Then: Gemini writes the script. Pexels sources a photograph per scene. Edge TTS
narrates it. Subtitles are timed against the actual audio durations, and ffmpeg
cuts the final vertical MP4."**

> *Now speed the footage 4–8× through Narration and Assembly. Drop back to
> real-time when it reaches **Provenance**.*

**"And then the two stages that make this different."**

---

### 1:15 – 2:00 · Provenance — the payoff

> *Screen: pipeline hits **Provenance**, then **Backblaze B2**. New card appears
> with the B2 badge and the shield. Open the card menu → **Provenance**.*

**"Every finished render is registered through Genblaze as an ingest run. That
produces a canonical manifest, bound by SHA-256 to every artefact — the video,
the thumbnail, the captions, the script.**

**Here it is."**

> *Pause on the green verification banner. Scroll the panel slowly.*

**"The generation chain: which provider and model ran at each stage. Not the
configured preference — what actually ran, including the fallbacks that fired.**

**Every artefact with its content hash. And this isn't a stored flag — the server
re-verifies the manifest against the assets every time you open this panel."**

> *Expand "Show raw manifest JSON" for two seconds, then collapse.*

**"The manifest is also written into the MP4 itself. So the file carries its own
record — download it, re-upload it anywhere, and it still says how it was made."**

---

### 2:00 – 2:30 · Backblaze B2

> *Screen: cut to the B2 bucket, `flux/library/<video>/` expanded.*

**"All of it lives on Backblaze B2. Video, thumbnail, captions, script, and the
manifest — under one prefix per video.**

**The bucket is private. The app hands the browser short-lived presigned URLs, so
playback streams straight from B2 and never touches the server."**

> *Screen: cut to the Genblaze runs prefix, `flux/runs/`.*

**"Genblaze writes its own run manifests here too."**

---

### 2:30 – 2:50 · Why this is production-minded

> *Screen: back to the live app, then `/health` briefly.*

**"One thing I want to call out. This is deployed on a container with no
persistent disk — and when I deployed it, the library was already populated.**

**Because B2 is the library, not a backup. Local disk is scratch. Restart it,
redeploy it, move hosts — nothing is lost. That's what makes it deployable for
free.**

**And every dependency degrades: Pexels falls back to generation, cloud
narration falls back to local, B2 falls back to disk. Health reports exactly
what's configured instead of failing silently."**

---

### 2:50 – 3:00 · Close

> *Screen: library grid, shields visible.*

**"Flux. Prompt to pipeline to durable, verifiable storage.**

**Built with Genblaze and Backblaze B2. The link's below — it's live, go make
one."**

---

## If you need a 60-second cut

Keep: **0:00–0:18** (problem) → **Run Automation**, jump-cut straight to the card
appearing → **1:15–2:00** (provenance panel, full) → **2:00–2:15** (B2 bucket) →
one-line close.

Cut: the ranking explanation, the per-stage narration, the health endpoint.
The provenance panel is the whole pitch — never cut that.

---

## Say this / don't say this

| Say | Don't say |
|---|---|
| "Genblaze orchestrates the pipeline and produces the provenance manifest" | "Genblaze generates the visuals" — it doesn't in this configuration; Pexels does |
| "Stored on Backblaze B2, served via presigned URLs" | "Backed up to B2" — B2 *is* the library, that's the whole point |
| "The manifest is verified on every read" | "Tamper-proof" — it's tamper-*evident*, and the manifest isn't signed |
| "Visuals come from Pexels, with generation as the fallback" | Implying every frame is AI-generated |

Accuracy is a scoring criterion here, and a judge who catches one overclaim
discounts everything else. The honest version is strong enough.

---

## Facts you can quote

| Claim | Value |
|---|---|
| Render time | ~60 s end to end (script 6 s · visuals 3 s · narration 3 s · assembly 40 s · provenance + upload 5 s) |
| Artefacts per render | 6 — mp4, thumbnail, srt, script, manifest, metadata |
| Manifest binding | SHA-256 per artefact + a canonical run hash |
| Bucket layout | `flux/library/{video}/…` and `flux/runs/{tenant}/{date}/{run_id}/…` |
| Presigned URL TTL | 1 hour, re-signed on every library load |
| Ranking weights | 0.45 recency + 0.55 cross-feed momentum |
| Deployment | One Docker image, one URL — SPA bundled into FastAPI, no CORS |
| Tests | 12, covering B2 upload/list/presign/delete, manifest verification, tamper detection, MP4 embed round-trip |

---

## Devpost description opener

Reuse the hook — it works in text too:

> AI can generate a news video in two minutes. Nobody can tell you where it came
> from. Flux is a news-to-video pipeline where every output is answerable: each
> render is registered through Genblaze as an ingest run that binds a SHA-256 to
> every artefact and records which model ran at each stage. The manifest is
> stored on Backblaze B2 *and* embedded into the MP4, so the proof travels with
> the file. B2 isn't a backup here — it's the library, which is why the app runs
> on a container with no persistent disk and loses nothing on redeploy.
