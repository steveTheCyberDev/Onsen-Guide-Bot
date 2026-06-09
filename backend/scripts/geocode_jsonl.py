"""
Geocode onsen records and write lat/long back into the source JSONL.

Usage (from the backend/ dir, using the venv):
    .venv/bin/python scripts/geocode_jsonl.py --file ../data/okinawa_springs.jsonl

Reusable across region files. For each record it builds the query
`f"{name} {location}"` (Japanese, full location INCLUDED — generic onsen names
like "山田温泉" geocode to the wrong prefecture without it), calls the shared
geocoding service, and appends `latitude`/`longitude` to each record.

On a GeocodingError the record's lat/long are set to null, the run continues,
and the record is collected for a "NEEDS MANUAL REVIEW" summary. The file is
rewritten in place (same --file path), preserving field order and Japanese text.
"""

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv
load_dotenv(BACKEND_DIR / ".env")

from services.geocoding.geocoding_service import geocode
from core.exceptions import GeocodingError


def geocode_file(jsonl_path: Path) -> None:
    """Geocode every record in jsonl_path and rewrite the file in place."""
    records = [
        json.loads(line)
        for line in jsonl_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    print(f"Loaded {len(records)} records from {jsonl_path.name}\n")

    rows = []          # (name, query, lat, lng) for the verification table
    needs_review = []  # records that failed to geocode

    for record in records:
        name = record.get("name", "")
        location = record.get("location", "")
        # Full location is INCLUDED on purpose — generic names geocode wrong without it.
        query = f"{name} {location}".strip()

        try:
            result = geocode(query)
            record["latitude"] = result["latitude"]
            record["longitude"] = result["longitude"]
        except GeocodingError as e:
            record["latitude"] = None
            record["longitude"] = None
            needs_review.append((name, query, str(e)))

        rows.append((name, query, record["latitude"], record["longitude"]))

    # Rewrite in place, one JSON object per line, Japanese kept readable.
    with jsonl_path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print_table(rows)
    print_summary(len(records), needs_review)


def print_table(rows: list[tuple]) -> None:
    """Print one verification row per record with a clickable Google Maps link."""
    print("VERIFICATION TABLE")
    print("=" * 100)
    for name, query, lat, lng in rows:
        if lat is None or lng is None:
            maps_link = "(no result — manual review)"
            coords = "null | null"
        else:
            maps_link = f"https://www.google.com/maps?q={lat},{lng}"
            coords = f"{lat} | {lng}"
        print(f"name:  {name}")
        print(f"query: {query}")
        print(f"coords: {coords}")
        print(f"map:   {maps_link}")
        print("-" * 100)


def print_summary(total: int, needs_review: list[tuple]) -> None:
    geocoded = total - len(needs_review)
    print(f"\nSUMMARY: {geocoded}/{total} geocoded successfully.")
    if needs_review:
        print(f"\nNEEDS MANUAL REVIEW ({len(needs_review)}):")
        for name, query, err in needs_review:
            print(f"  - {name}  (query: {query})  -> {err}")
    else:
        print("All records geocoded — no manual review needed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Geocode onsen JSONL and write lat/long back into the file"
    )
    parser.add_argument("--file", required=True, help="Path to .jsonl file")
    args = parser.parse_args()

    geocode_file(Path(args.file))
