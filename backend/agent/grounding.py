"""Shared anti-fabrication grounding contract for the RECOMMEND-style LLM brains.

Single source of truth for the STRICT GROUNDING RULES that keep the recommend-mode
brain (``agent/workflow/analyze.py::analyze_onsen``) and the trip-planner analyze
brain (``agent/trip/analyze.py::analyze_itinerary``) from fabricating onsen facts.
Both brains import this constant and append it AFTER their own task-framing intro,
so the anti-fabrication contract can only be hardened in ONE place — no silent
drift where one brain keeps a weaker version after the other is tightened
post-incident (the exact risk this module exists to remove).

The projection helper + per-candidate schema are shared here too, since both brains
sent byte-identical copies. ``GuideResult`` / ``TripGuideResult`` stay per-brain
because their ``recommendation`` framing differs (pick one onsen vs. judge a whole
itinerary), but both reference the shared :class:`OnsenAnalysis`.

Neutral by design: depends only on ``agent/`` schemas + pydantic, so ``agent/trip/``
imports it WITHOUT reaching into ``agent/workflow/`` internals — the established
layering stance (cf. ``itinerary.py`` keeping ``_ONSEN_FIELDS`` / ``_to_hotel``
local for the same reason).
"""

from pydantic import BaseModel, Field

from agent.schemas import OnsenResult

# Max characters of the spa_quality/description sent per candidate. Long marketing
# descriptions add tokens without improving judgement, so we truncate. Module-level
# named constant (not a magic literal) per project config conventions.
DESC_MAX_CHARS = 280

# The anti-fabrication contract, shared VERBATIM by both brains. This is the body of
# grounding bullets that follows each brain's own task-framing intro. This exact
# text is the live recommend path's grounding block — DO NOT weaken it. To harden
# grounding after an incident, edit it HERE and BOTH brains inherit the change.
# (The per-brain "the recommendation may compare …" bullet is intentionally NOT part
# of this shared block — it is domain-specific and lives with each brain.)
STRICT_GROUNDING_RULES = (
    "STRICT GROUNDING RULES — these override any instinct to be more helpful:\n"
    "- Every pro and con MUST be directly supported by the LITERAL text of that "
    "onsen's provided fields (name, spring type, location, description). Do NOT "
    "infer amenities, scenery, baths, views, atmosphere, crowds, or activities "
    "from the onsen's NAME, from its LOCATION, or from general knowledge about "
    "the area or the spring type.\n"
    "- If an onsen's description is 'none provided', you usually cannot ground "
    "any specific pro or con — return EMPTY pros and cons for that onsen rather "
    "than guessing. It is correct and expected for an onsen to have no pros/cons.\n"
    "- Never invent facilities, prices, opening hours, tattoo policies, transport, "
    "baths, views, or any fact not present in the data.\n"
    "- Refer to each onsen by its given index so your analysis can be matched back.\n"
    "- Keep pros/cons short (a few words each)."
)


class OnsenAnalysis(BaseModel):
    """Per-candidate judgement, tied back to the input by ``index``.

    Shared by both brains' structured outputs (``GuideResult`` /
    ``TripGuideResult``) so the pros/cons contract has one definition.
    """

    index: int = Field(description="0-based index of the onsen in the provided list.")
    pros: list[str] = Field(
        default=[],
        description=(
            "Short positives supported by the LITERAL provided fields. Empty when "
            "the fields (esp. an absent description) don't support any — do not infer."
        ),
    )
    cons: list[str] = Field(
        default=[],
        description=(
            "Short caveats supported by the LITERAL provided fields. Empty when "
            "the fields (esp. an absent description) don't support any — do not infer."
        ),
    )


def project_candidates(onsens: list[OnsenResult]) -> str:
    """Render a compact, token-lean projection of the candidates for the prompt.

    Sends only name, spring_type, location, and a truncated description. Omits
    coordinates and URLs — they carry no judgement value, just tokens. Shared by
    both brains so the projection format has one definition.
    """
    lines: list[str] = []
    for i, o in enumerate(onsens):
        desc = (o.spa_quality or "").strip()
        if len(desc) > DESC_MAX_CHARS:
            desc = desc[:DESC_MAX_CHARS].rstrip() + "…"
        lines.append(
            f"[{i}] {o.name}\n"
            f"    Spring type: {o.spring_type or 'unknown'}\n"
            f"    Location: {o.location or 'unknown'}\n"
            f"    Description: {desc or 'none provided'}"
        )
    return "\n".join(lines)
