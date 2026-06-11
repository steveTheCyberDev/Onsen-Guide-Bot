"""Spring-type → benefit one-liner lookup (a plain dict, NOT embeddings).

This is a tiny deterministic lookup, not a vector search. Each value is a concise,
one-line summary distilled from the corresponding ``## <Spring>`` section of
``backend/data/knowledge/spring_types.md`` — kept consistent with that doc (which
is already sourced against the Japan Secret Hot Springs Association). No new
claims are invented here.

The KEYS MUST match the English ``spring_type`` labels emitted at ingest
(``scripts/ingest.py`` ``SPA_QUALITY_MAP`` values) verbatim, so a future
recommend-time lookup on an onsen's ``spa_quality_en`` lines up exactly. A drift
guard test (``tests/test_spring_benefits.py``) asserts every ``SPA_QUALITY_MAP``
value has an entry here.

Consumed by ``recommend`` later (and optionally injected into the ``ask`` answer
when the question is spring-type-specific). It lives in ``agent/workflow/``
because it is an agent-layer reference table, not an external service.
"""

# Keys are copied verbatim from scripts/ingest.py SPA_QUALITY_MAP values.
SPRING_BENEFITS: dict[str, str] = {
    "Simple Spring": (
        "A mild, low-mineral spring that is gentle on the skin — often recommended "
        "for sensitive skin, older bathers, and first-timers, and associated with "
        "relaxation and easing general fatigue, muscle stiffness, and nerve pain."
    ),
    "Bicarbonate Spring": (
        'A "beauty bath" (bijin-no-yu) that softens the skin and removes surface '
        "impurities, traditionally associated with smoother skin and a refreshed "
        "feeling after bathing."
    ),
    "Chloride Spring": (
        'A salt-rich "heat bath" whose warming effect lingers after you leave — '
        "traditionally associated with keeping the body warm, easing chills, and "
        "soothing minor cuts."
    ),
    "Sulfate Spring": (
        'A sulfate-mineral "wound bath" traditionally associated with soothing the '
        "skin, helping minor cuts, and supporting circulation — a well-rounded, "
        "gentle mineral bath."
    ),
    "Iron Spring": (
        "An iron-rich spring (often reddish-brown from oxidised minerals) with a "
        "strong warming effect, traditionally associated with warming the body, "
        "anemia, and menstrual disorders."
    ),
    "Sulfur Spring": (
        'A distinctive "rotten egg"–smelling, often milky spring that softens '
        "hardened skin — traditionally associated with skin conditions, high blood "
        "pressure, arteriosclerosis, and gout; best enjoyed with good ventilation."
    ),
    "Acidic Spring": (
        "A strongly acidic spring with powerful antibacterial qualities, "
        "traditionally associated with chronic skin conditions — potent but harsh "
        "on sensitive skin and eyes, so rinse with fresh water afterward."
    ),
    "Radon Spring": (
        'A mild "radium bath" with only trace natural radioactivity, traditionally '
        "associated with joint, muscle, and nerve pain plus skin conditions and "
        "high blood pressure — usually a gentle, restorative soak."
    ),
    "Iodine Spring": (
        "A less common iodine-containing spring (sometimes yellowish) valued as a "
        "mineral-rich soak with its own distinctive character."
    ),
    "Carbon Dioxide Spring": (
        'A fizzy "bubble/ramune bath" whose carbon dioxide dilates blood vessels — '
        "traditionally associated with supporting circulation and lowering blood "
        "pressure, with a gentle warming effect even at lower temperatures."
    ),
    "Aluminium Spring": (
        "A less common aluminium-mineral spring, usually colourless-to-yellow and "
        "acidic, with strong antibacterial qualities and an astringent, tightening "
        "feel on the skin."
    ),
    "Copper-Iron Spring": (
        "A rarer mineral spring containing both copper and iron that oxidises to a "
        "yellow-brown sediment and generates a warming effect (though the minerals "
        "can reduce how well soap lathers)."
    ),
    "Other": (
        "Springs that do not fall neatly into the standard mineral categories, or "
        "whose classification is not specified — best enjoyed on their own terms."
    ),
}


def benefits_for(spring_type: str | None) -> str | None:
    """Return the benefit one-liner for a spring type, or None if unknown.

    Returns None for ``None`` input and for any key not present in
    ``SPRING_BENEFITS`` (e.g. an unmapped or multi-type ``spa_quality_en`` string),
    so callers can cleanly skip injecting benefit text when there is no exact match.
    """
    if spring_type is None:
        return None
    return SPRING_BENEFITS.get(spring_type)
