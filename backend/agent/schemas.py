"""Shared response schemas for the /chat flow.

Neutral, framework-agnostic Pydantic models used across the live workflow engine
(``agent/workflow/pipeline.py``), the trip planner (``agent/trip/``), and the API
layer (``api/routes/chat.py``). Kept in their own module so nothing has to import a
heavier engine module just to reference the response shape.

The ``/chat`` response contract is: ``reply`` + ``onsens[]`` + ``hotels[]`` +
``recommendation``. ``pros``/``cons``/``recommendation`` are ADDITIVE fields the
recommend-mode ``analyze_onsen`` brain populates; search/ask leave them empty/None.
"""

from pydantic import BaseModel, ConfigDict, Field


class OnsenResult(BaseModel):
    # Defense-in-depth: reject unexpected/injected keys instead of silently
    # dropping them (Pydantic v2's default is extra="ignore"). Every construction
    # site projects field-by-field onto an allow-list (pipeline._ONSEN_FIELDS,
    # itinerary.onsen_results_from_itinerary), so no legitimate caller splats a
    # dict carrying extras — a raise here means an unexpected/attacker key slipped in.
    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Name in English")
    location: str | None = Field(default=None, description="City in English")
    spring_type: str
    spa_quality: str
    lat: float | None = Field(
        default=None,
        description=(
            "Latitude of the onsen, copied VERBATIM from the search_onsen tool "
            "output's Latitude line. Null if the tool output has no coordinates."
        ),
    )
    lng: float | None = Field(
        default=None,
        description=(
            "Longitude of the onsen, copied VERBATIM from the search_onsen tool "
            "output's Longitude line. Null if the tool output has no coordinates."
        ),
    )
    # V2.5 RECOMMEND additions (ADDITIVE). Populated only by the recommend-mode
    # analyze_onsen brain (agent/workflow/analyze.py); search mode leaves them empty.
    pros: list[str] = Field(
        default=[],
        description="Grounded positives for this onsen (recommend mode only).",
    )
    cons: list[str] = Field(
        default=[],
        description="Grounded caveats for this onsen (recommend mode only).",
    )


class HotelResult(BaseModel):
    # Defense-in-depth: reject unexpected/injected keys (see OnsenResult). Both
    # construction sites (_to_hotel in pipeline.py and itinerary.py) build this
    # field-by-field, so extras never reach a legitimate call.
    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Name in English")
    originalName: str = Field(description="Name in Original Language")
    location: str | None = Field(default=None, description="City and prefecture in English")
    hotelSpecial: str | None = Field(default=None, description="Display in English")
    price: str | None = Field(default=None, description="Minimum price per night in yen, numbers only e.g. '4200' — hotel results only")
    image: str | None = Field(default=None, description="Image URL — hotel results only")
    url: str | None = Field(default=None, description="Link to more information")
    lat: float | None = Field(default=None, description="Latitude of hotel")
    lng: float | None = Field(default=None, description="Longitude of hotel")


class AgentResponse(BaseModel):
    # Defense-in-depth: reject unexpected/injected keys (see OnsenResult). Every
    # construction site (pipeline.py, trip/agent.py) sets fields explicitly, so a
    # raise here signals an unexpected key rather than a legitimate response shape.
    model_config = ConfigDict(extra="forbid")

    reply: str = Field(
        description=(
            "One sentence summary only. Example: 'Found 3 onsens and 10 nearby hotels in Naha, Okinawa.' "
            "No markdown, no bullet points, no listing of individual results."
        )
    )
    onsens: list[OnsenResult] = Field(
        default=[],
        description=(
            "Onsen MUST come verbatim from the search_onsen tool output ONLY. "
            "If search_onsen was not called or returned no matches, this MUST be an "
            "empty list. NEVER invent or recall onsen names, locations, spring types, "
            "spa quality, or descriptions from your own knowledge. "
            "Each onsen from the tool has name, location, spring type, spa quality and sales point."
        )
    )
    hotels: list[HotelResult] = Field(
        default=[],
        description=(
            "Hotels MUST come verbatim from the search_rakuten_onsen tool output ONLY. "
            "If search_rakuten_onsen was not called or returned no results, this MUST be an "
            "empty list. NEVER invent or recall hotel names, URLs, images, prices, or "
            "coordinates, and never use placeholder/example URLs (e.g. anything containing "
            "'example.com'). For each hotel from the tool: set `image` from the tool's "
            "`hotelImageUrl` field, set `url` from the tool's `url` field (the Rakuten "
            "hotelInformationUrl), and copy the tool's `lat` and `lng` verbatim — do not "
            "invent or round them. "
            "Translate name, hotelSpecial and location to English; keep the Japanese name in "
            "originalName. Leave any field null if the tool output does not provide it."
        )
    )
    # V2.5 RECOMMEND addition (ADDITIVE, optional). A top-level grounded pick
    # produced by the recommend-mode analyze_onsen brain. None in search/ask modes.
    recommendation: str | None = Field(
        default=None,
        description="Top-level recommendation across the returned onsen (recommend mode only).",
    )
