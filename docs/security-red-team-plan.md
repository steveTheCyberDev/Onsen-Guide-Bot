# Security & Red-Team Plan — Onsen Guide Bot

> Status: **draft / not started (2026-06-25).** Captured so the intent is on record and the work is
> scoped when it's time. Today the project has **grounding/safety controls** (the anti-fabrication
> guardrail + eval gate) but **no security red-teaming** — no prompt-injection/jailbreak tests, no SAST,
> no dependency/secret scanning in CI. This doc is the plan to close that, **least-effort-first**.
>
> Scope note: this is **defensive** security testing of our own app (authorised). Same spirit as the
> autonomy ladder elsewhere in the repo — add the cheapest control that covers a real risk, climb only
> when a concrete gap demands it.

---

## 0. Why bother (and why now is fine to defer)

This is a low-blast-radius app: read-only onsen/hotel info, no user accounts, no write paths, no PII
store. So security is **not** an emergency. But three things make a written plan worth it:

1. **There is a real prompt-injection surface** — `/chat` feeds user text *and* retrieved KB prose into
   an LLM. The `ask` mode does RAG over markdown we control today, but "untrusted text reaches the
   model" is exactly the class the AI-103 **Prompt Shields** material is about.
2. **Two public endpoints** (`/chat`, `/hotels`) call paid external APIs (OpenAI, Rakuten) — abuse =
   cost. Rate limiting exists; it should be *proven* against abuse, not assumed.
3. **Portfolio value** — "I threat-modelled my own LLM app and red-teamed the injection surface" is a
   senior-level talking point, and it dovetails with the AI-103 responsible-AI/red-teaming topics
   (Prompt Shields, Content Safety, PyRIT / AI red-teaming agent).

---

## 1. Current state (snapshot — proves the thinking)

**Already present (security-adjacent):**
- `backend/tests/test_auth.py` — auth on protected routes.
- `backend/tests/test_rate_limit.py`, `test_limiter_key.py` — rate limiting + key derivation.
- `backend/tests/test_http_retry.py`, `test_llm_max_retries.py` — resilience / retry-with-backoff.
- Anti-fabrication guardrail (system prompt + structured-output schema) and the **eval release gate**
  (`scripts/eval_flow.py`, `backend/tests/test_eval_flow.py`) — a **grounding** control, *not* security.

**Absent (the gap this doc covers):**
- No prompt-injection / jailbreak test suite.
- No output-safety check (Content-Safety-style) on `analyze`/`ask` responses.
- No SAST (bandit/semgrep), dependency audit (pip-audit / `npm audit`), or secret scanning in `ci.yml`
  (CI = backend pytest + frontend Vitest + LangSmith eval gate only).
- No documented threat model.

---

## 2. Threat model (STRIDE-lite, scoped to what we actually run)

| Surface | Threat | Realistic? | Existing control | Gap |
|---|---|---|---|---|
| `/chat` user message | Prompt injection / jailbreak ("ignore instructions, output X") | Med | system-prompt discipline; deterministic data path (LLM can't assemble facts) | no test pinning resistance |
| `ask` RAG over KB docs | **Indirect** injection via poisoned KB markdown | Low now (we author the KB) | none | becomes Med if KB ever ingests external content |
| `/chat`, `/hotels` | Abuse → cost (spam expensive OpenAI/Rakuten calls) | Med | rate limiting | not load/abuse-tested |
| Output | Harmful / off-brand / PII-leaking model output | Low | grounding guardrail | no output safety classifier |
| Secrets | API keys leaked (repo, logs, client) | Med | `core/config.py` env split; keys server-side | no secret scan; no log-redaction check |
| Dependencies | Vulnerable Python/npm package | Med | none | no `pip-audit` / `npm audit` in CI |
| `/hotels` | Coordinate/param injection or SSRF-style abuse via Rakuten call | Low | deterministic, typed params | confirm input validation/bounds |

**Top priorities:** (1) prompt-injection resistance test on `/chat`, (2) dependency + secret scanning in
CI (cheap, high coverage), (3) abuse/cost rate-limit proof.

---

## 3. Plan (phased, least-effort-first)

### Phase 1 — Cheap CI hygiene (hours, high coverage)
- [ ] Add **`pip-audit`** (backend) + **`npm audit --omit=dev`** (frontend) as CI steps (non-blocking
      first, then blocking on high severity).
- [ ] Add **secret scanning** — `gitleaks` action (or enable GitHub secret scanning) on push/PR.
- [ ] Enable **Dependabot** (or `pip` + `npm` update PRs).
- [ ] Add **bandit** (Python SAST) as a non-blocking informational job to start.

#### Phase 1 findings

- **`chromadb 1.5.9` → CVE-2026-45829 (pip-audit).** Max-severity **pre-auth RCE** in
  ChromaDB's **standalone HTTP server**. **Not exploitable here:** this app uses the
  **embedded `PersistentClient`** (in-process, `vectorstore/store.py`), never the
  network `chromadb.HttpClient` / `chroma run` server, so the vulnerable request path
  is not reachable. **No patched version exists yet** → keep `chromadb` pinned as-is,
  re-run `pip-audit` periodically, and **bump the moment a fixed release ships**.
  (Confirmed no `HttpClient` / server usage in the codebase; if a client/server split
  is ever introduced, this finding is immediately in-scope.)

### Phase 2 — Prompt-injection red-team suite (the core gap)
- [ ] Build `backend/tests/test_prompt_injection.py` (or a `scripts/redteam_*.py` harness): a fixed set
      of adversarial `/chat` inputs — instruction-override, system-prompt-exfil, "return JSON that
      breaks the schema", role-play jailbreaks, data-exfil ("print your instructions").
- [ ] **Assertions** = the no-fabrication / grounding contract still holds (every onsen real or empty),
      no system-prompt leakage, schema stays valid, no off-task action. Reuse the eval harness pattern
      from `eval_flow.py` (fixed cases + ground truth).
- [ ] Add an **indirect-injection** case: a KB doc containing "ignore previous instructions" and assert
      the `ask` answer ignores it. Cheap insurance for when the KB grows.

### Phase 3 — Output safety + abuse proof
- [ ] Optional **output classifier** on `analyze`/`ask` (Azure AI Content Safety or a lightweight check)
      — tie-in to AI-103 Content Safety; gate behind a flag, off by default.
- [ ] **Abuse/cost test**: simulate burst traffic to `/chat` and `/hotels`, assert the limiter trips and
      cost stays bounded (no runaway OpenAI/Rakuten calls).
- [ ] Confirm `/hotels` validates coordinate bounds / param types (reject garbage before the Rakuten call).

### Phase 4 — Formalise (only if the app grows / gets accounts)
- [ ] Run **PyRIT** (Microsoft AI red-teaming) or the Foundry **AI red-teaming agent** against a staging
      `/chat` for systematic coverage — promote the best findings into Phase-2 fixed cases (the same
      "failed trace → eval case" loop used for quality).
- [ ] Threat-model refresh if user accounts, write paths, or external-content ingestion are added.

---

## 4. Definition of done (for a first pass)
- CI runs dependency audit + secret scan + bandit (Phase 1).
- A committed prompt-injection test suite that **fails loudly** if grounding/leak protections regress
  (Phase 2).
- This doc updated with results + any findings, and a line in `PROJECT_JOURNEY.md` (engineering
  challenge / senior-readiness) recording the work.

## 5. Explicitly out of scope (for now)
- Pen-testing infra/Railway, DDoS protection, WAF — platform concerns, revisit at real traffic.
- Anything requiring user accounts/PII — none exist yet.

---

### AI-103 cross-reference (study ↔ project)
Prompt Shields (jailbreak + indirect injection) → Phase 2. Content Safety (harm categories, severity) →
Phase 3 output classifier. PyRIT / AI red-teaming agent → Phase 4. Doing this project work *is*
hands-on practice for the exam's responsible-AI domain.
