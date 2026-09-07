"""
One-time backfill: resolve a Google ``place_id`` for every ingested onsen
record, via the Places API (New) Text Search endpoint
(services/places/places_service.py::search_place_id), queried by
``f"{name} {location}"`` — the same query shape already proven in
scripts/geocode_jsonl.py for the original lat/lng backfill.

NOTE: Places API (New) is a SEPARATE billing/SKU from the Geocoding API the
app already uses at ingest time — it must be enabled on the Google Cloud
project independently before this script's real (non-dry-run) calls will
succeed. It's purely a prerequisite step for the still billing-gated Places
ratings/photos work in docs/next-steps.md Track B.

Idempotent / resumable: records that already carry a non-empty ``place_id``
are skipped, so an interrupted run can just be re-run without re-spending
API calls. Writes are atomic per file (temp file + os.replace) so a crash
mid-run can never leave a half-written jsonl file.

Usage (run from the backend/ directory, or the project root):

  # Report how many records need a place_id, no API calls, no writes:
  python backend/scripts/backfill_place_ids.py --dry-run

  # Test on a handful of records first, one region only:
  python backend/scripts/backfill_place_ids.py --regions okinawa --limit 3

  # Full backfill across every *_springs.jsonl in backend/data/:
  python backend/scripts/backfill_place_ids.py

Safe-to-import guarantee: all network I/O and file writes are guarded by
``if __name__ == "__main__"``.
"""

import argparse
import json
import sys
from pathlib import Path

# ── Path setup ───────────────────────────────────────────────────────────────
BACKEND_DIR = Path(__file__).parent.parent  # .../backend
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env")

from core.config import settings  # noqa: E402
from core.exceptions import PlacesError  # noqa: E402
from services.places.places_service import search_place_id  # noqa: E402

DATA_DIR = settings.data_dir


# ── Helpers ───────────────────────────────────────────────────────────────────

def discover_all_slugs() -> list[str]:
    """Return slugs for every *_springs.jsonl present in DATA_DIR."""
    return sorted(
        p.stem.replace("_springs", "")
        for p in DATA_DIR.glob("*_springs.jsonl")
    )


def resolve_data_file(slug: str) -> Path:
    """Return the Path for a given slug, or raise with a clear message."""
    path = DATA_DIR / f"{slug}_springs.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"No data file found for slug '{slug}': expected {path}")
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill a Google place_id onto every onsen record, by "
            "reverse-geocoding its stored latitude/longitude. With no "
            "arguments, processes every *_springs.jsonl in backend/data/."
        )
    )
    parser.add_argument(
        "--regions",
        nargs="+",
        metavar="SLUG",
        help="Space-separated slugs to process (e.g. okinawa tokai). Default: all.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Only resolve the first N records missing a place_id, per file (for testing).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report how many records need a place_id, without calling the API or writing.",
    )
    return parser.parse_args()


def backfill_file(path: Path, limit: int | None, dry_run: bool) -> dict:
    """Resolve + write back place_id for records in one jsonl file."""
    records = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    already = sum(1 for r in records if r.get("place_id"))
    to_process = [r for r in records if not r.get("place_id")]
    if limit is not None:
        to_process = to_process[:limit]

    print(
        f"[backfill_place_ids] {path.name}: {len(records)} records total, "
        f"{already} already have a place_id, {len(to_process)} to resolve this run"
    )

    if dry_run:
        return {"resolved": 0, "failed": 0, "skipped": already}

    resolved = 0
    failed = 0
    for record in to_process:
        name, location = record.get("name"), record.get("location")
        if not name or not location:
            print(f"[backfill_place_ids]   SKIP (no name/location): {record.get('name')}")
            failed += 1
            continue
        try:
            record["place_id"] = search_place_id(name, location)
            resolved += 1
        except PlacesError as exc:
            print(f"[backfill_place_ids]   FAILED: {record.get('name')} — {exc}")
            failed += 1

    if resolved:
        # Atomic write: build the full file in a temp path, then replace —
        # a crash mid-run leaves the original file untouched.
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        tmp_path.replace(path)
        print(f"[backfill_place_ids]   wrote {resolved} new place_id(s) back to {path.name}")

    return {"resolved": resolved, "failed": failed, "skipped": already}


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = parse_args()

    slugs = args.regions if args.regions else discover_all_slugs()

    data_files: list[Path] = []
    errors: list[str] = []
    for slug in slugs:
        try:
            data_files.append(resolve_data_file(slug))
        except FileNotFoundError as exc:
            errors.append(str(exc))
    if errors:
        print("\n[backfill_place_ids] ERROR — the following slugs could not be resolved:\n")
        for err in errors:
            print(f"  {err}")
        sys.exit(1)

    print(f"[backfill_place_ids] Processing {len(data_files)} file(s): "
          f"{', '.join(f.name for f in data_files)}\n")

    totals = {"resolved": 0, "failed": 0, "skipped": 0}
    for data_file in data_files:
        result = backfill_file(data_file, args.limit, args.dry_run)
        for key in totals:
            totals[key] += result[key]
        print()

    print(
        f"[backfill_place_ids] Done. resolved={totals['resolved']} "
        f"failed={totals['failed']} already-had={totals['skipped']}"
    )
