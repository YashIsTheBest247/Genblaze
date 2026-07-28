# Flux — Deployment

**Live: https://genblaze-production.up.railway.app**

Goal: one public URL that serves both the UI and the API, with every generated
asset stored durably on **Backblaze B2**.

The Docker image at the repository root builds the React SPA and serves it from
FastAPI, so there is nothing to deploy separately and no CORS to configure.
Because B2 owns the media, the host's filesystem can be completely ephemeral.

---

## 1. Prerequisites

### 1a. Backblaze B2 (required)

The library lives here. Free tier includes 10 GB.

1. Sign up: <https://www.backblaze.com/sign-up/cloud-storage>
2. **B2 Cloud Storage → Buckets → Create a Bucket**
   - Name it (e.g. `flux-media`)
   - **Files in Bucket are: Private** — Flux hands out short-lived presigned
     URLs, so the bucket never needs to be public
3. **Application Keys → Add a New Application Key**
   - Restrict it to the bucket you just made
   - Access: **Read and Write**
   - Copy `keyID` and `applicationKey` — the secret is shown **once**
4. Note the bucket's **Endpoint** (e.g. `s3.us-west-004.backblazeb2.com`);
   the region is the middle segment: `us-west-004`

| Variable | Value |
|---|---|
| `B2_KEY_ID` | the `keyID` |
| `B2_APP_KEY` | the `applicationKey` |
| `B2_BUCKET` | your bucket name |
| `B2_REGION` | e.g. `us-west-004` |

### 1b. Script model (required)

`GEMINI_API_KEY` from <https://aistudio.google.com/app/apikey> (free tier).
Alternatively set `SCRIPT_PROVIDER=ollama` for a fully local model.

### 1c. Optional

| Variable | Effect |
|---|---|
| `GMI_API_KEY` | Turns on Genblaze cloud image **and** narration models. Worth adding: cloud narration removes torch/Kokoro from the critical path, cutting render time and memory. |
| `PEXELS_API_KEY` | Stock photography for scene visuals (free, fast) |
| `YOUTUBE_TOKEN_JSON` | Contents of `secrets/youtube_token.json`, enables publishing |

---

## 2. Recommended: Railway (this is what the live deploy runs on)

### Why not the "free" tiers

Rendering is RAM-heavy: local Kokoro TTS pulls in torch (~490 MB of deps) and the
render peaks near 1.5 GB. That rules out most free tiers, which is worth knowing
before you burn an afternoon on one:

| Host | Free tier | Verdict |
|---|---|---|
| Hugging Face Spaces | **Static only** — Docker Spaces now require PRO | ✗ cannot run the backend |
| Render | 512 MB | ✗ OOMs mid-render |
| Koyeb | 512 MB | ✗ same |
| Google Cloud Run | Generous, but CPU is throttled outside a request | ✗ renders run as background tasks and get killed |
| **Railway** | **$5 trial credit, 30 days, no card** | ✓ |

### Steps

1. Push this repository to GitHub.
2. 🔗 <https://railway.com> → sign up with GitHub → **New Project** →
   **Deploy from GitHub repo**. Railway auto-detects the root `Dockerfile`;
   [`.dockerignore`](.dockerignore) keeps old renders out of the build context
   (198 MB → 17 MB).
3. **Variables → Raw Editor** and paste all six at once:
   ```
   GEMINI_API_KEY=...
   B2_KEY_ID=...
   B2_APP_KEY=...
   B2_BUCKET=...
   B2_REGION=us-east-005
   PEXELS_API_KEY=...
   ```
   Saving variables triggers a redeploy. **The app builds and serves fine
   without them — it just can't render.** `/health` reports exactly which are
   missing, so check it after the redeploy rather than assuming.
4. **Settings → Networking → Generate Domain.** Railway does not expose the
   service publicly by default. That domain is the URL for judges.
5. Verify `/health` (§6), then run 2–3 renders to populate the library.

First build takes 8–15 minutes — `pip install` of torch dominates. Later deploys
reuse that layer unless `requirements.txt` changes.

Budget: roughly $10/GB-month, so the $5 trial covers about two weeks of a 1 GB
always-on instance. If it runs out, [render.yaml](render.yaml) deploys the same
image on Render's $7/month Starter plan.

---

## 3. Render

```bash
# render.yaml at the repo root is already configured
```

1. **New → Blueprint**, point it at this repository
2. Render reads [render.yaml](render.yaml) and creates one Docker web service
3. Fill in the `sync: false` secrets in the dashboard
4. **Use the `starter` plan or larger** — the 512 MB free plan OOMs mid-render

## 4. Fly.io / Cloud Run

Both build the root `Dockerfile` unchanged.

- **Fly.io**: `fly launch --dockerfile Dockerfile`, then
  `fly secrets set GEMINI_API_KEY=... B2_KEY_ID=... B2_APP_KEY=... B2_BUCKET=... B2_REGION=...`
  and size the VM with `fly scale memory 2048`.
- **Cloud Run**: `gcloud run deploy flux --source . --memory 2Gi --timeout 900
  --min-instances 1 --no-cpu-throttling`. Both flags matter: renders run as
  background tasks that outlive the HTTP response, so scale-to-zero or throttled
  CPU would kill one mid-flight.

## 5. Split deploy (frontend on Vercel)

Still supported if you prefer it:

1. Deploy the backend by any method above.
2. Vercel → import the repo, **Root Directory: `frontend`**
3. Set `VITE_API_BASE_URL` to the backend URL (build-time)
4. Set `CORS_ORIGINS` on the backend to your Vercel URL (no trailing slash)

---

## 6. Verify the deployment

```bash
curl https://genblaze-production.up.railway.app/health
```

```jsonc
{
  "status": "healthy",
  "ready": true,             // a script model is configured
  "durable_storage": true,   // B2 reachable — this is the one to check
  "checks": {
    "script_llm": true,
    "backblaze_b2": true,
    "genblaze": true,
    "genblaze_sink": true,   // Genblaze is writing manifests to B2
    "stock_images": true,
    "gmi_cloud": false
  }
}
```

`durable_storage: false` means the B2 credentials are missing or wrong — the app
will still render, but into ephemeral local disk. `backblaze_b2.error` in the
same response says why.

Then, in the UI:

1. Open the site → the **Library** header shows `Backblaze B2 · <bucket>` and
   `Genblaze provenance`
2. **Automation → Run Automation** with top-N = 1
3. Watch the pipeline advance through **Provenance** and **Backblaze B2**
4. When the card appears it carries a **B2** badge and a verified shield
5. Card menu → **Provenance** shows the manifest: generation chain, per-artefact
   SHA-256, and the verification result

---

## 7. Notes and gotchas

- **One render at a time.** The render lock and live status are in-process, so
  run a single worker (the Dockerfile pins `--workers 1`). Horizontal scaling
  would need Redis or a task queue.
- **First render is slow.** With local Kokoro TTS the model downloads once
  (~350 MB) and is then cached. Setting `GMI_API_KEY` avoids this entirely.
- **Memory.** Peak usage lands around 1.5 GB with local TTS, well under 1 GB
  with cloud TTS. `FLUX_FFMPEG_THREADS=2` trims the peak further.
- **Presigned URLs expire** after `B2_URL_TTL_SECONDS` (default 1 h). The
  library re-signs on every refresh, so this only matters for links you copy
  out of the app.
- **Never commit** `backend/.env` or `backend/secrets/` — both are gitignored.
