"""Trip-planner slot-filling schema + structured extraction (V3 PR3b).

Two Pydantic models and the extraction call that feeds the elicit-loop:

  * ``TripSlots``  — the canonical, ACCUMULATING trip state. It is what
    ``TripState.slots`` holds, so it is checkpointed per
    ``thread_id = session_id`` and carried across turns. The three
    "required" slots (``regions``/``nights``/``dates_or_season``) are nullable here
    on purpose: partial state must be representable while a multi-turn conversation
    fills them. "Required" is enforced by :func:`missing_required` (the elicit
    gate), NOT by Pydantic construction.
  * ``SlotUpdate`` — the structured-output target for ONE message's extraction: a
    delta where every field is ``None`` unless the latest message mentions it. It is
    merged into the running ``TripSlots`` by :func:`merge_slots`, so slots already
    gathered on earlier turns persist and the new message only fills/updates what it
    actually names.

The extraction call mirrors ``agent/workflow/intent.py``: a module-level
``ChatOpenAI(...).with_structured_output(...)`` built once at import, run at
``temperature=0`` for deterministic extraction (same rationale as the intent-parser
fix). Tests patch the module-level ``_llm`` so no real OpenAI call is made.

Layering: imports shared LLM plumbing + ``core.config`` only — never ``api/`` and
no ``services/`` calls in this slice.
"""

import logging
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from core.config import settings

logger = logging.getLogger(__name__)

# Enum vocabularies for the optional slots (§2). Declared once as type aliases and
# reused by both TripSlots and the SlotUpdate delta so the two models never drift.
Party = Literal["solo", "couple", "family", "friends"]
Budget = Literal["budget", "mid", "luxury"]
Transport = Literal["car", "train", "mixed"]
Pace = Literal["relaxed", "packed"]

# How a message's named regions apply to the regions gathered so far. The LLM
# classifies the INTENT (language understanding); :func:`merge_slots` does the set
# arithmetic in Python (deterministic state merge) — see the module docstring.
#   "replace" — the message states the complete new set ("Hokkaido only").
#   "add"     — the message adds to what we have ("what about Hokkaido too?").
#   "remove"  — the message drops some of what we have ("drop Nagano").
RegionOp = Literal["replace", "add", "remove"]

# Applied when the extraction returns regions WITHOUT an explicit op. "replace" is
# the historical (pre-fix) behaviour and the safest default: a bare region list is
# taken as the complete set, so a mis-classified turn narrows rather than silently
# accumulating regions the traveller never asked to keep.
_DEFAULT_REGION_OP: RegionOp = "replace"

# The slots that BLOCK planning, in elicit priority order. The elicit node asks for
# the first one still missing (regions → nights → dates_or_season). Single source of
# truth for both missing_required() and next_question().
REQUIRED_SLOTS: tuple[str, ...] = ("regions", "nights", "dates_or_season")


class TripSlots(BaseModel):
    """Accumulating trip-planning slots (§2 of the plan).

    Held in ``TripState.slots`` and checkpointed per ``thread_id = session_id``, so a
    follow-up answer on a later turn merges into slots gathered earlier. All fields
    have defaults so a partially-filled instance is always constructible — the
    "required" slots are gated by :func:`missing_required`, not by Pydantic.
    """

    # --- required for planning (nullable → represent partial state) --------
    regions: list[str] = Field(
        default_factory=list,
        description="English prefecture name(s) the trip should cover (e.g. ['Gifu', 'Nagano']).",
    )
    nights: int | None = Field(
        default=None,
        description="Total number of nights for the trip (drives itinerary length).",
    )
    dates_or_season: str | None = Field(
        default=None,
        description="An ISO date range OR a season label (e.g. 'autumn', 'early November').",
    )

    # --- optional (refine with §2 defaults; no downstream wiring yet) -------
    party: Party = Field(
        default="couple",
        description="Who is travelling: solo, couple, family, or friends.",
    )
    budget: Budget = Field(
        default="mid",
        description="Rough budget tier: budget, mid, or luxury.",
    )
    spring_or_scenery_prefs: str = Field(
        default="",
        description="Free-text spring-type / scenery preferences (e.g. 'sulfur springs, mountain views').",
    )
    mobility_transport: Transport = Field(
        default="mixed",
        description="Primary transport mode: car, train, or mixed.",
    )
    must_haves: list[str] = Field(
        default_factory=list,
        description="Hard requirements (e.g. 'private bath', 'tattoo-friendly').",
    )
    pace: Pace = Field(
        default="relaxed",
        description="Trip pace: relaxed (~1 onsen-stop/night) or packed.",
    )


class SlotUpdate(BaseModel):
    """One message's extraction delta — every field ``None`` unless mentioned.

    The structured-output target for :func:`extract_slots`. ``None`` means "the
    latest message said nothing about this slot", so :func:`merge_slots` leaves the
    prior value untouched.

    ``regions`` carries ONLY the prefectures the latest message names, paired with
    ``regions_op`` saying how they apply to the regions gathered so far. The LLM
    therefore never has to do set arithmetic (it used to be asked to "return the
    full merged list", which made "Hokkaido only" indistinguishable from "Hokkaido
    too") — :func:`merge_slots` computes the new list deterministically in Python.
    """

    regions: list[str] | None = Field(
        default=None,
        description=(
            "The English prefecture name(s) THIS message names — not the merged "
            "list; null if the message says nothing about location. Pair with "
            "regions_op to say whether they replace, add to, or are removed from "
            "the regions gathered so far."
        ),
    )
    regions_op: RegionOp | None = Field(
        default=None,
        description=(
            "How `regions` applies to the regions gathered so far: 'replace' when "
            "the message states the complete new set ('Hokkaido only', 'just "
            "Hokkaido', 'actually make it Gifu'); 'add' when it adds to them ('what "
            "about Hokkaido too', 'also include Nagano'); 'remove' when it drops "
            "some of them ('drop Nagano', 'not Shizuoka'). Null when regions is "
            "null; defaults to 'replace' when regions is given without an op."
        ),
    )
    nights: int | None = Field(
        default=None, description="Number of nights if the message states one; else null."
    )
    dates_or_season: str | None = Field(
        default=None,
        description="ISO date range or season label if the message gives timing; else null.",
    )
    party: Party | None = Field(default=None, description="Party type if mentioned; else null.")
    budget: Budget | None = Field(default=None, description="Budget tier if mentioned; else null.")
    spring_or_scenery_prefs: str | None = Field(
        default=None,
        description="Spring-type / scenery preferences if mentioned; else null.",
    )
    mobility_transport: Transport | None = Field(
        default=None, description="Transport mode if mentioned; else null."
    )
    must_haves: list[str] | None = Field(
        default=None,
        description="Hard-requirement list if the message names any; else null.",
    )
    pace: Pace | None = Field(default=None, description="Pace if mentioned; else null.")


# Human-friendly follow-up for each required slot. Exactly ONE is returned per turn
# by the elicit node (the first still-missing slot in REQUIRED_SLOTS order).
_ELICIT_QUESTIONS: dict[str, str] = {
    "regions": "Which area(s) of Japan would you like your onsen trip to cover?",
    "nights": "How many nights are you planning to travel?",
    "dates_or_season": "When are you thinking of going — do you have dates, or at least a season in mind?",
}


def missing_required(slots: TripSlots) -> list[str]:
    """Return the required slots still unfilled, in elicit priority order.

    A slot counts as missing when it is empty/None: ``regions == []``,
    ``nights is None``, or ``dates_or_season`` empty/None. Order follows
    ``REQUIRED_SLOTS`` so the caller can ask for the first one deterministically.
    """
    missing: list[str] = []
    if not slots.regions:
        missing.append("regions")
    if slots.nights is None:
        missing.append("nights")
    if not slots.dates_or_season:
        missing.append("dates_or_season")
    return missing


def next_question(slots: TripSlots) -> str | None:
    """The single follow-up to ask this turn, or ``None`` if nothing is missing."""
    missing = missing_required(slots)
    if not missing:
        return None
    return _ELICIT_QUESTIONS[missing[0]]


# --- region validation (V3, 2026-07-12 "reject early") -----------------------
# Unknown / non-Japan regions are rejected at slot-filling, BEFORE the plan node,
# rather than surfacing as an itinerary "no onsen found for X" footnote. The source
# of truth for a valid region is the set of prefectures actually INGESTED in Chroma
# (services.retrieval.known_prefectures) — passed in as ``known`` so this layer
# stays free of a services import at module scope and is trivially testable.


def invalid_regions(slots: TripSlots, known: frozenset[str]) -> list[str]:
    """Return the slot's regions that are NOT in the known ingested set (case-insensitive).

    Empty when ``regions`` is empty (nothing to validate — that's a *missing*
    regions case handled by :func:`missing_required`, not an *invalid* one). Order
    preserves the user's stated regions so the elicit message names them naturally.
    """
    known_lower = {k.strip().lower() for k in known}
    return [r for r in slots.regions if r.strip().lower() not in known_lower]


def _join_and(names: list[str]) -> str:
    """Join names with commas and a trailing 'and' ("A", "A and B", "A, B and C")."""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f" and {names[-1]}"


def _example_prefectures(known: frozenset[str], k: int = 3) -> list[str]:
    """A few real ingested prefectures to suggest — never one we lack.

    Drawn from ``known`` (sorted for determinism) so the hint can only ever
    propose prefectures we actually have data for.
    """
    return sorted(known)[:k]


def region_invalid_message(invalid: list[str], known: frozenset[str]) -> str:
    """The tailored follow-up when the user named unknown/non-Japan region(s).

    Names every invalid region ("Texas", "Texas and California"), explains we only
    plan Japanese onsen trips, and suggests real ingested prefectures. Approved copy
    (2026-07-12); the example prefectures come from :func:`_example_prefectures` so
    we never suggest a prefecture we lack.
    """
    subject = _join_and(invalid)
    verb = "isn't" if len(invalid) == 1 else "aren't"
    examples = _example_prefectures(known)
    hint = f" (e.g. {', '.join(examples)})" if examples else ""
    return (
        f"{subject} {verb} somewhere I cover — I only plan Japanese onsen trips. "
        f"Which Japanese prefecture(s) would you like?{hint}"
    )


def should_elicit(slots: TripSlots, known: frozenset[str]) -> bool:
    """Whether this turn must elicit rather than plan.

    True when a required slot is missing OR when ``regions`` are present but any is
    invalid. The region-validity check is what makes an otherwise-"complete" turn
    (all three required slots filled) still elicit — so a mixed "Gifu and Texas"
    request never reaches the plan node.
    """
    return bool(missing_required(slots)) or bool(invalid_regions(slots, known))


def elicit_message(slots: TripSlots, known: frozenset[str]) -> str | None:
    """The single message to return this turn, honouring region-validity precedence.

    Precedence (matches ``REQUIRED_SLOTS`` order, regions first):
      * regions present but invalid → the tailored region-invalid message;
      * otherwise → the first missing required slot's question
        (``next_question``: "Which area(s)?" when regions are missing entirely,
        else nights/dates).
    Returns ``None`` only when nothing is missing AND all regions are valid — i.e.
    the caller should route to ``plan``.
    """
    invalid = invalid_regions(slots, known)
    if slots.regions and invalid:
        return region_invalid_message(invalid, known)
    return next_question(slots)


def _dedupe(regions: list[str]) -> list[str]:
    """Drop case-insensitive duplicates, keeping the FIRST spelling and the order."""
    seen: set[str] = set()
    out: list[str] = []
    for r in regions:
        key = r.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(r.strip())
    return out


def apply_region_op(current: list[str], named: list[str], op: RegionOp | None) -> list[str]:
    """Compute the new region list from the current one + this message's regions.

    The deterministic half of the ADD-vs-REPLACE fix: the LLM says WHAT the message
    named and WHICH intent it expressed; the set arithmetic happens here, in Python.
    Matching is case-insensitive ("hokkaido" removes "Hokkaido") while the stored
    spelling is preserved.

    Args:
        current: The regions gathered so far.
        named: The regions the latest message named.
        op: ``"replace"`` (default) / ``"add"`` / ``"remove"`` — see :data:`RegionOp`.

    Returns:
        The new region list. May be empty when the message removed every region —
        ``missing_required`` then re-elicits for an area, which is the honest
        outcome of "drop everything".
    """
    named = _dedupe(named)
    if op == "add":
        return _dedupe(current + named)
    if op == "remove":
        drop = {r.lower() for r in named}
        return [r for r in current if r.strip().lower() not in drop]
    # "replace" (and the None default): the message states the complete new set.
    return named


def merge_slots(current: TripSlots, update: SlotUpdate) -> TripSlots:
    """Merge a one-message ``SlotUpdate`` delta onto the running ``TripSlots``.

    Semantics: a field the message did not mention comes back ``None`` (or, for the
    list slots, an empty list) and is skipped, so the prior value persists. Any
    field the message DID name overwrites the current value. This is what makes
    slots accumulate across turns instead of being reset each message.

    ``regions`` is the exception — it is not a blind overwrite. The message's named
    regions are combined with the accumulated ones per ``update.regions_op``
    (:func:`apply_region_op`), so a follow-up can REPLACE or NARROW the trip
    ("Hokkaido only", "drop Nagano") and not only ADD to it. ``regions_op`` is a
    routing signal for this merge, never a slot: it is consumed here and never
    written onto ``TripSlots``.
    """
    data = current.model_dump()
    delta = update.model_dump()
    named_regions = delta.pop("regions", None)
    region_op = delta.pop("regions_op", None)
    # A non-empty region list is the only thing that changes regions: an empty list
    # (or None) means "this message mentioned no place", which must never wipe or
    # re-interpret the regions gathered so far.
    if named_regions:
        data["regions"] = apply_region_op(
            current.regions, named_regions, region_op or _DEFAULT_REGION_OP
        )
    for field, value in delta.items():
        if value is None:
            continue
        # Empty list from the delta ("nothing mentioned") must not wipe a prior list.
        if isinstance(value, list) and not value:
            continue
        data[field] = value
    return TripSlots(**data)


_INSTRUCTIONS = (
    "You extract trip-planning details from a traveller's latest message about a "
    "Japanese hot-spring (onsen) trip, to fill a slot form. You are shown the slots "
    "gathered so far. Return ONLY the slots the LATEST message mentions or changes; "
    "leave every other field null so previously-known values are preserved. Rules:\n"
    "- regions: ONLY the English prefecture name(s) THIS message names (e.g. 'Gifu', "
    "'Nagano', 'Shizuoka'), without the word 'Prefecture'. Do NOT merge them with the "
    "regions gathered so far — that is done for you. Map a city or area to its "
    "prefecture (e.g. Kanazawa -> Ishikawa). Null if the message names no location.\n"
    "- regions_op: how those regions apply to the ones gathered so far — "
    "'replace' when the message states the complete new set ('Hokkaido only', 'just "
    "Hokkaido', 'actually make it Gifu instead'), 'add' when it adds to them ('what "
    "about Hokkaido too', 'also include Nagano'), 'remove' when it drops some of them "
    "('drop Nagano', 'not Shizuoka'). A message that both narrows and names what to "
    "drop ('Hokkaido only, drop Nagano and Kanazawa') is a 'replace' with the kept "
    "region(s). Null when regions is null.\n"
    "- nights: an integer number of nights if stated (e.g. '5 nights' -> 5); else null.\n"
    "- dates_or_season: an ISO date range if given, otherwise a season/month label "
    "(e.g. 'autumn', 'early November'); null if no timing is mentioned.\n"
    "- party/budget/mobility_transport/pace: only if clearly indicated, using the "
    "allowed values; else null.\n"
    "- spring_or_scenery_prefs: free-text preferences (spring type, scenery, mood) if "
    "mentioned; else null.\n"
    "- must_haves: hard requirements like 'private bath' or 'tattoo-friendly' if named; "
    "else null.\n"
    "Never invent values the traveller did not express."
)

# Built once at import — mirrors agent/workflow/intent.py::_llm. Uses the cheap
# intent_model knob (default gpt-4o-mini); temperature=0 for deterministic extraction
# (same rationale as the intent-parser fix); structured output binds SlotUpdate so
# ainvoke returns a validated delta directly. Tests patch this module-level object.
_llm = ChatOpenAI(
    model=settings.intent_model,
    api_key=settings.openai_api_key,
    temperature=0,
    stream_usage=True,
    max_retries=settings.llm_max_retries,
).with_structured_output(SlotUpdate)


async def extract_slots(
    message: str, current: TripSlots, callbacks: list | None = None
) -> TripSlots:
    """Extract slots from ``message`` and merge them onto ``current``.

    Runs one structured-output LLM call (``SlotUpdate``) with the current slots as
    context, then merges the delta so prior turns persist and only mentioned slots
    change. Returns the updated ``TripSlots``.

    Args:
        message: The latest user message.
        current: The slots accumulated so far (from checkpointed state).
        callbacks: Optional LangChain callbacks (e.g. usage capture); threaded into
            the run config so token usage can be recorded by the caller.

    Returns:
        A new ``TripSlots`` = ``current`` merged with the message's extracted delta.
    """
    context = (
        "Slots gathered so far (JSON):\n"
        f"{current.model_dump_json()}\n\n"
        f"Latest traveller message:\n{message}"
    )
    messages = [
        SystemMessage(content=_INSTRUCTIONS),
        HumanMessage(content=context),
    ]
    run_config: dict = {
        "run_name": "trip-extract-slots",
        "tags": ["trip", "slots", f"model:{settings.intent_model}"],
        "metadata": {"node": "gather", "intent_model": settings.intent_model},
    }
    if callbacks:
        run_config["callbacks"] = callbacks
    update: SlotUpdate = await _llm.ainvoke(messages, config=run_config)
    merged = merge_slots(current, update)
    logger.info(
        "extract_slots | missing_required=%s | regions=%s (named=%s op=%s) | nights=%s",
        missing_required(merged),
        merged.regions,
        update.regions,
        update.regions_op,
        merged.nights,
    )
    return merged
