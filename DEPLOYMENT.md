# Onsen Guide Bot — Deployment Plan (V1 MVP)

> Status: **DRAFT for review.** Nothing has been deployed and no infra/CI files
> exist yet. Backend ships first. Authored from the devops-cicd-steve plan
> (2026-06-02).

## Decisions locked in

- **Backend host:** Railway (Hobby, ~$5/mo) — persistent disk for ChromaDB, no
  cold starts, simple volume mounts, clear Postgres upgrade path for V2.
- **Frontend host:** Vercel (free) — native Vite support, reads `.nvmrc`
  (Node 20.16.0), automatic PR preview deploys. *(Deployed after the backend.)*
- **Vector store:** keep embedded ChromaDB on a Railway persistent volume.
- **Environments:** production only for V1 (Vercel PR previews serve as free
  frontend "staging").
- **LLM/provider:** stay on OpenAI direct for V1.
- **Deferred to V2+ (deliberate, not now):** integrate Azure services
  (Azure OpenAI, Azure AI Search). **Rejected:** rewriting the backend to
  TypeScript / LangChain.js.
- **Order:** deploy and verify the **backend first**, then the frontend.

---

## Stack being deployed

- **backend/** — FastAPI (uvicorn), entry `api.main:app` on port 8000.
  Endpoints: `/chat` (GPT-4o LangChain ReAct agent + ChromaDB RAG),
  `/hotels` (deterministic Rakuten lookup), `/health`.
- **Vector store** — embedded ChromaDB persisted to `backend/chroma_db/`
  (SQLite-backed), populated by `backend/scripts/ingest.py` from JSONL in
  `backend/data/`.
- **frontend/** — Vite + React static build (covered later, separate doc/phase).

---

## Backend deployment (Phase-by-phase)

### Phase 0 — Pre-flight (before any infra)
- [ ] Review and approve this plan.
- [x] Decide the two open questions below (Q1 ChromaDB population, Q2 chat history). — **Both resolved 2026-06-02. See Open questions section.**
- [ ] Add `RAKUTEN_HOTEL_URL` to the root `.env.example` (it is a required field
      in `config.py` but missing from the example).

### Phase 1 — CI test gate (backend)
- [ ] Add `.github/workflows/ci.yml` with a **backend-tests** job:
  - checkout → setup Python 3.12 → `pip install -r backend/requirements.txt`
  - `pytest` with `working-directory: backend`
  - cache pip deps keyed on `backend/requirements.txt` hash
  - `paths` filter so it runs only on `backend/**` changes
- [ ] No real secrets needed — `conftest.py` sets dummy env vars for tests.
- [ ] Verify the job passes on the current branch.
- [ ] (Frontend test job added in the frontend phase; both become required
      status checks via branch protection on `main`.)

### Phase 2 — Containerize the backend
- [ ] Write `backend/Dockerfile`:
  - base `python:3.12-slim`, workdir `/app`
  - copy `requirements.txt` → `pip install --no-cache-dir`
  - copy app source: `api/ agent/ services/ vectorstore/ core/`
  - **do NOT** copy `chroma_db/`, `data/`, `.env`, `.venv/`, `scripts/`
    (the persistent volume provides `chroma_db/` at runtime)
  - expose 8000
  - `CMD uvicorn api.main:app --host 0.0.0.0 --port 8000`
  - all secrets injected as runtime env vars, never baked into the image
  - note: image will be ~1–2 GB (chromadb pulls onnxruntime) — normal for V1.

### Phase 3 — Provision Railway
- [ ] Create Railway project + Hobby plan.
- [ ] Add the backend service pointed at `backend/Dockerfile`.
- [ ] Attach a persistent disk, **mount at `/app/chroma_db`**.
- [ ] Set runtime env vars (see Secrets below).

### Phase 4 — Populate ChromaDB

**Decision: Strategy 1A — one-off Railway job after deploy (Q1 resolved).**
The ingest runs inside the deployed Railway container, writing directly to the
persistent volume mounted at `/app/chroma_db`.  No local-to-remote copy needed.

**Launch subset: okinawa + tokai only (~220 records total).**
The remaining 8 regions are deferred until after initial launch validation.

Run as a Railway one-off job (Railway dashboard → your service → "Run Job",
or via Railway CLI `railway run`):

```bash
# From the project root inside the Railway container:
python scripts/ingest_regions.py
```

This ingests `okinawa_springs.jsonl` (~3 records) and `tokai_springs.jsonl`
(~217 records) in 20-record translation batches.  The call is idempotent
(ChromaDB upsert keyed on `detail_url`) — safe to re-run without duplicating data.

To expand coverage later, either add slugs to `ACTIVE_REGIONS` in
`backend/scripts/ingest_regions.py` and re-run, or run:

```bash
# Ingest all 10 regions at once:
python scripts/ingest_regions.py --all

# Or ingest specific additional regions:
python scripts/ingest_regions.py --regions kanto kinki kyushu
```

Required env vars (already set in Railway env panel): `OPENAI_API_KEY` (used
for embeddings and translation via GPT-4o-mini), `ANTHROPIC_API_KEY`,
`GOOGLE_MAPS_API_KEY`, `RAKUTEN_APP_ID`, `RAKUTEN_ACCESS_KEY`,
`RAKUTEN_HOTEL_URL`.

### Phase 5 — Verify backend
- [ ] `GET /health` on the Railway URL returns `{"status": "ok"}`.
- [ ] Smoke a real `/chat` and `/hotels` request; check Railway logs for
      missing-env-var or CORS errors.
- [ ] Configure Railway health check on `/health` for auto-restart.

### Phase 6 — Wire backend deploy into CI
- [ ] On merge to `main` (after backend-tests pass): build Docker image,
      push to GitHub Container Registry (`ghcr.io`, free), trigger Railway deploy.
- [ ] Add a post-deploy smoke step: `curl --fail https://<backend>/health`.
- [ ] GitHub secret needed: `RAILWAY_TOKEN` (`GITHUB_TOKEN` covers ghcr push).

---

## Secrets & environment variables

**Backend runtime (Railway env panel):**
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY` (reserved)
- `GOOGLE_MAPS_API_KEY`
- `RAKUTEN_APP_ID`
- `RAKUTEN_ACCESS_KEY`
- `RAKUTEN_HOTEL_URL`
- `CORS_ORIGINS` — exact Vercel frontend URL, **no trailing slash** (set once the
  frontend URL exists).

`pydantic-settings` reads these from the environment exactly as it reads the
local `.env` today — no code change required.

**CI (GitHub Actions secrets):**
- Test jobs: none (dummy env via `conftest.py`).
- Deploy job: `RAILWAY_TOKEN`.

---

## Frontend (later phase — summary only)

Vercel project rooted at `frontend/`, build-time env `VITE_API_URL`
(= Railway backend URL) and `VITE_GOOGLE_MAPS_API_KEY` (restrict by domain in
GCP Console). Frontend test job added to CI; both test jobs required before
merge to `main`. Full frontend phase detailed when we get there.

---

## Open questions

1. **ChromaDB population state.** DECIDED (2026-06-02): **Strategy 1A** —
   run ingest as a one-off Railway job post-deploy, writing to the persistent
   volume at `/app/chroma_db`.  Launch subset is **okinawa + tokai** (~220
   records); full 10-region ingest deferred to post-launch.  See Phase 4 for
   exact commands.
2. **In-memory chat history.** DECIDED (2026-06-02): **in-memory accepted for
   V1.**  `chat_service.py` history resets on restart — this is a known
   trade-off for a demo/MVP.  Persistence via Redis or Postgres deferred to V2
   (Railway addon when needed).

Lower-stakes (can defer): custom domain? Maps key domain restriction (do once
Vercel URL exists)? Post-merge branching model (feature branches off `develop`)?

---

## What changes at V2/V3 (awareness only)

- **V2:** chat-history persistence (Redis/Postgres); optional staging env;
  begin Azure service integration (Azure OpenAI and/or Azure AI Search).
- **V3:** ChromaDB → HTTP server mode (or Azure AI Search) for multi-replica
  backends; dedicated container registry with layer caching if build time bites.
