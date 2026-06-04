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
