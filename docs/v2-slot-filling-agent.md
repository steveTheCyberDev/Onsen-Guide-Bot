# V2 Design — Slot-Filling Agent

> **Status:** Design / not yet built. Captures the planned evolution from the V1
> open-ended ReAct agent to a slot-filling (form-filling) agent built on a
> LangGraph `StateGraph`.

---

## Motivation

V1 uses `create_react_agent` (a LangGraph prebuilt): the agent freely reasons and
decides when to call tools. That's flexible and was fast to ship, but it has two
problems for *this* product:

1. **Under-specified queries retrieve badly.** RAG filters on `prefecture_en`
   (see the location challenge in the README). If the user says *"find me a nice
   onsen"* with no location, the agent guesses or calls `search_onsen` with thin
   parameters → weak or wrong results.
2. **Open-ended loops are unpredictable and cost more** — extra reasoning turns,
   extra tool calls, harder to test.

A slot-filling agent fixes the accuracy problem at the source: **collect the
parameters a good search needs *before* retrieving**, asking the user a targeted
follow-up when something required is missing.

---

## Workflow vs. agent — and why V1/V2 is really a *workflow*

The deeper realisation behind this redesign: **for V1/V2's scope, this was never
an "agent" problem.** It's worth being precise about the words, because they get
conflated:

- **Workflow** — *you* wire the steps; the path is fixed code/edges. The LLM only
  fills slots at predefined points.
- **Agent** — the *LLM* decides which tools to call, and in what order, looping
  until it judges itself done. You need this only when the tool sequence **isn't
  knowable ahead of time.**

V1 uses a real agent (`create_react_agent`, an autonomous tool-loop). But the
actual flows are fixed pipelines:

1. *"onsen in X"* → extract prefecture → search vector DB → return.
2. *"hotels near this onsen"* → get coordinates → call Rakuten → return.

The only genuinely "agentic" decisions are tiny and bounded: did the user give a
location? do they want hotels too, or onsen only? which onsen does *"it"* refer
to? That's **intent/slot classification** — a single cheap LLM call (or rules),
not an autonomous loop. Using a ReAct agent for this is the same category of
over-reach as using GPT-4o to copy a database row.

**The autonomy ladder — use the least that solves the task:**

> rules → pipeline → **workflow-graph** → agent → multi-agent

V1 jumped straight to *agent*. That wasn't wrong — it was the right *bootstrapping*
move: maximally flexible while the query shapes were still unknown. But the shapes
are known now, so the honest V2 framing isn't "build a better agent" — it's
**"graduate down to a workflow."** A LangGraph `StateGraph` with hardcoded edges
*is* a workflow, even though it's still LangGraph; we keep LangGraph because it's
the V3 stepping stone, and drop only the autonomous prebuilt.

**When does the agent come back?** At **V3**, when autonomy genuinely earns its
cost — open-ended goals where the tool sequence can't be pre-wired: *"plan me a
5-day onsen trip with transport, budget, and weather"* (dynamic re-planning),
*"compare these regions and recommend."* That's the multi-agent orchestrator. For
"find onsen / find hotels," a workflow is the correct altitude.

This also directly attacks the measured latency bottleneck: the ReAct loop's
`reason → tool → observe → re-reason → structure` collapses to `one intent call +
deterministic work + one reply call` — fewer round-trips is the real speedup.
(Geocoding was *not* the bottleneck; see the perf note in `PROJECT_JOURNEY.md`.)

---

## Deterministic assembly — the LLM shouldn't copy data it already has

A workflow unlocks a second win that an autonomous agent makes awkward: building
the `onsens[]` result **in code**, not via the LLM.

Today there's a wasteful, risky round-trip. Watch the data change shape:

```
Chroma metadata (STRUCTURED)
   → retrieval_service flattens it to TEXT ("Name: …\nLocation: …\nLatitude: …")
      → LLM reads the text and RE-STRUCTURES it back into OnsenResult objects
```

The LLM is taking structured data we already have, that we deliberately turned
into text, and laboriously turning it back into structure — while we *hope* it
copies every field verbatim and invents nothing. That round-trip is the source of
the fabrication risk, the lat/lng float-mangling, and a big slice of output tokens.

**Fix: assemble the objects directly from retrieval, in code.**

```python
# retrieval_service.py — return objects, not a text blob
def query_onsen(query, prefecture=None) -> list[OnsenResult]:
    results = collection.query(...)
    docs, metas = results["documents"][0], results["metadatas"][0]
    return [
        OnsenResult(
            name        = meta.get("name_en") or meta.get("name"),
            location    = meta.get("city_en"),
            spring_type = meta.get("spa_quality_en", ""),
            spa_quality = meta.get("spa_quality_en", ""),
            sales_point = doc,                    # the embedded description
            lat         = meta.get("latitude"),   # exact float, not retyped
            lng         = meta.get("longitude"),
        )
        for doc, meta in zip(docs, metas)
    ]
```

Then the response's onsen list **is** that list; the LLM writes only the reply:

```python
AgentResponse.onsens = onsens_from_retrieval   # assembled by code
AgentResponse.reply  = llm_reply               # LLM writes ONLY the sentence
```

Every field maps from what we already store (`name_en`, `city_en`,
`spa_quality_en`, `latitude`, `longitude` in metadata; `sales_point` is the Chroma
*document*). What this kills outright:

- **Fabrication** — onsen come straight from the DB; the LLM *cannot* invent one.
  The project's biggest correctness win becomes structural, not prompt-dependent.
- **Float-mangling** — coordinates pass by reference, never retyped by the model.
- **Tokens / latency / cost** — the LLM stops re-emitting N onsen per response.
- **Prompt weight** — the heavy "copy verbatim / empty if none" onsen guardrail
  can mostly go.

**Why this needs the workflow:** in a ReAct agent the tool output flows *into the
LLM's context*, not back to orchestration code — so you can't easily keep the
structured list "outside" the model. The workflow's `search` node calls retrieval
directly and holds the objects, which is exactly what makes deterministic assembly
possible. Hotels may still need the LLM for translation (until the hotel-name
translation cache lands), but **onsen become fully deterministic.**

---

## Slots

| Slot | Required? | Used by | Notes |
|------|-----------|---------|-------|
| `prefecture` | **Yes** | `search_onsen` metadata filter | The one slot that most affects retrieval quality. |
| `spring_type` | No | ranking / filter | e.g. sulfur, chloride. Refines, not gates. |
| `area` / budget | No | hotel lookup | For the Rakuten step. |

Only `prefecture` gates the search. Optional slots refine results but never block.

### How the location slot gets filled — Google Places Autocomplete

The `prefecture`/location slot should **not** rely on the agent guessing the
user's intended place from free text (ambiguous names, typos, "near the beach").
V2 fills it with **Google Places Autocomplete** in the frontend: the user picks a
real place as they type, and we capture the exact `place_id` + coordinates.

This mirrors the structured-output decision in the README (#2) — don't make the
LLM guess what a deterministic UI control can capture exactly. Two wins:

- **Accuracy:** the location is a real, disambiguated place, not an NLP guess.
- **Fewer calls:** autocomplete returns coordinates directly, so the *input*
  location no longer needs a separate geocode.

> Two different location problems, two different fixes — don't conflate them:
> - **Input location** (where to search) → **Places Autocomplete** (accuracy).
> - **Output coordinates** (lat/lng of each *returned* onsen, for map markers) →
>   **ingest-time geocoding** (latency). This is the V1 bottleneck in README #3.
>
> *Tradeoff:* Places Autocomplete is billed per session and shifts location
> capture from pure chat toward a UI control — a minor product trade; accuracy
> wins.

---

## Graph shape (LangGraph `StateGraph`)

This is where we graduate from the `create_react_agent` prebuilt to a hand-built
`StateGraph` — which also sets up the V3 multi-agent direction. **Tools and
services are unchanged**; only the orchestration layer changes.

```
          ┌─────────────────────────────┐
          │           gather            │  inspect state for missing
          │  (extract slots from msg,   │  required slots
          │   ask follow-up if missing) │
          └──────────────┬──────────────┘
                         │ conditional edge
        required slots   │   required slots
        MISSING          │   COMPLETE
        (ask + END turn) │
                         ▼
          ┌─────────────────────────────┐
          │           search            │  call search_onsen (+ geocode,
          │     (existing V1 tools)     │  + search_rakuten_onsen)
          └──────────────┬──────────────┘
                         ▼
          ┌─────────────────────────────┐
          │           respond           │  structured AgentResponse
          │   (Pydantic, as V1 today)   │  (reply + onsens[] + hotels[])
          └─────────────────────────────┘
```

### State

```python
class AgentState(TypedDict):
    messages: list           # conversation history
    prefecture: str | None   # required slot
    spring_type: str | None  # optional slot
    onsens: list             # filled by search node
    hotels: list             # filled by search node
```

### Nodes

- **`gather`** — extract any slot values present in the latest user message into
  state. (LLM call with a small extraction prompt, or structured output into the
  slot schema.) The location slot is preferably filled from the frontend's Places
  Autocomplete selection (`place_id` + coordinates) rather than NLP extraction —
  see "How the location slot gets filled" above.
- **`search`** — runs only once required slots are present; calls the existing V1
  tools/services exactly as today.
- **`respond`** — emits the same Pydantic `AgentResponse` the frontend already
  renders, so no frontend change is needed.

### Edges

- `gather → (conditional)`:
  - **missing required slot** → emit a targeted question
    (*"Which prefecture or area are you interested in?"*) and **end the turn**
    (waits for the user's next message, which re-enters `gather`).
  - **all required slots present** → `search`.
- `search → respond → END`.

---

## Tradeoffs (why this is a deliberate choice, not strictly "better")

| | ReAct (V1, now) | Slot-filling (V2) |
|---|---|---|
| Flexibility | High — handles anything | Lower — follows a defined flow |
| Predictability | Low — can wander / mis-call tools | High — deterministic path |
| Cost / latency | Higher (open-ended loops) | Lower (fewer, targeted calls) |
| Under-specified queries | Weak (guesses) | Strong (asks first) |

ReAct was the right V1 trade: fast to build, maximally flexible while the query
shapes were still unknown. Once those shapes are known, slot-filling becomes the
better trade — more predictable, cheaper, and accurate on vague queries — at the
cost of conversational flexibility.

---

## Migration notes

- `create_react_agent(...)` → an explicit `StateGraph(AgentState)` with the nodes
  above.
- **No change** to `services/` or `tools/` — the layering holds; only `agent/`
  changes.
- **No frontend change** — `respond` keeps emitting the same `AgentResponse`
  schema.
- This `StateGraph` is the same primitive V3 multi-agent uses, so V2 is a stepping
  stone, not a throwaway.

---

## Open questions

- Slot extraction: one combined LLM extraction call vs. structured-output per
  turn — measure cost/accuracy.
- How many follow-ups before falling back to a best-effort search (avoid
  interrogating the user)?
- Should `spring_type` ever gate, or always remain a refinement?
- Autocomplete fallback: if the user types a location in chat without picking an
  Autocomplete suggestion, do we still NLP-extract it, or nudge them to select?
