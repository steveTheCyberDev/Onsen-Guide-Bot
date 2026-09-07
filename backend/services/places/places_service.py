"""Google Places API (New) — Text Search + Place Details.

Separate API/billing SKU from the Geocoding API (services/geocoding/) — must
be enabled on the Google Cloud project independently. Not wired into any live
/chat path yet; a prerequisite/building block for the still-deferred Places
ratings work in docs/next-steps.md Track B.

Text Search (search_place_id) resolves a NAMED business/POI by name + area,
used ONLY to backfill place_id onto onsen records (scripts/backfill_place_ids.py)
— more reliable than reverse-geocoding a coordinate, which just returns
"whatever address is nearest this point", not necessarily the onsen itself.

Place Details (get_place_rating) then reads rating/review data for an
already-resolved place_id.

BILLING NOTE: requesting ``reviewSummary`` triggers Google's "Place Details
Enterprise" SKU — a HIGHER-cost tier than the base Place Details SKU (which
covers plain fields like rating/userRatingCount alone). Confirm this is the
intended cost tier before calling get_place_rating at scale across the whole
dataset. reviewSummary is also an AI-generated (Gemini) synthesis, not raw
review text — Google's terms expect its accompanying disclosureText to be
shown alongside it if surfaced to end users.
"""

from core.config import settings
from core.exceptions import PlacesError
from services.http_retry import get_with_retries, post_with_retries

PLACES_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
PLACE_DETAILS_URL = "https://places.googleapis.com/v1/places"

# Places API (New) requires an explicit field mask on every request — it
# controls both what's returned AND what's billed, so keep it minimal.
_SEARCH_FIELD_MASK = "places.id,places.displayName"
_DETAILS_FIELD_MASK = "id,displayName,rating,userRatingCount,reviewSummary"


def search_place_id(name: str, location: str) -> str:
    """Resolve a Google ``place_id`` via Places Text Search.

    Mirrors the query shape already proven in scripts/geocode_jsonl.py
    (``f"{name} {location}"``) — full location included on purpose, since a
    generic onsen name alone can resolve to the wrong prefecture.
    """
    query = f"{name} {location}".strip()
    response = post_with_retries(
        PLACES_TEXT_SEARCH_URL,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": settings.google_places_api_key,
            "X-Goog-FieldMask": _SEARCH_FIELD_MASK,
        },
        json={"textQuery": query},
        timeout=10,
    )
    if response.status_code != 200:
        raise PlacesError(
            f"Places Text Search failed for {query!r}: "
            f"HTTP {response.status_code} — {response.text}"
        )

    data = response.json()
    places = data.get("places") or []
    if not places:
        raise PlacesError(f"No place found for {query!r}")

    return places[0]["id"]


def get_place_rating(place_id: str) -> dict:
    """Fetch rating, review count and AI review summary for a place_id.

    Returns a dict with ``rating`` (float | None), ``user_rating_count``
    (int | None), ``review_summary`` (str | None — the Gemini-generated
    synthesis text) and ``review_summary_disclosure`` (str | None — the
    disclosure text Google expects shown alongside the summary if it's
    surfaced to end users).

    See the module docstring's BILLING NOTE — this triggers the Place
    Details Enterprise SKU, not the base Place Details SKU.
    """
    response = get_with_retries(
        f"{PLACE_DETAILS_URL}/{place_id}",
        headers={
            "X-Goog-Api-Key": settings.google_places_api_key,
            "X-Goog-FieldMask": _DETAILS_FIELD_MASK,
        },
        timeout=10,
    )
    if response.status_code != 200:
        raise PlacesError(
            f"Place Details failed for place_id={place_id!r}: "
            f"HTTP {response.status_code} — {response.text}"
        )

    data = response.json()
    summary = data.get("reviewSummary") or {}
    return {
        "rating": data.get("rating"),
        "user_rating_count": data.get("userRatingCount"),
        "review_summary": (summary.get("text") or {}).get("text"),
        "review_summary_disclosure": (summary.get("disclosureText") or {}).get("text"),
    }
