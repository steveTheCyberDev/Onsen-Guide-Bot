"""
One-time backfill: resolve rating + review summary for every onsen record
that already has a ``place_id``, via Places API (New) Place Details
(services/places/places_service.py::get_place_rating).

BILLING NOTE: this calls the Place Details Enterprise SKU (reviewSummary is
requested) — see the module docstring in places_service.py. Each place_id is
requested at most once per record; already-rated records are skipped, so
re-running this script never re-spends the call.

Idempotent / resumable: records that already carry a non-null ``rating`` are
skipped. Writes are atomic per file (temp file + os.replace).

Usage (run from the backend/ directory, or the project root):

  # Report how many records need a rating, no API calls, no writes:
  python backend/scripts/backfill_place_ratings.py --regions okinawa --dry-run

  # Resolve ratings for records that already have a place_id, one region:
  python backend/scripts/backfill_place_ratings.py --regions okinawa

  # Full backfill across every *_springs.jsonl in backend/data/:
  python backend/scripts/backfill_place_ratings.py

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
from services.places.places_service import get_place_rating  # noqa: E402

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
            "Backfill rating/review summary onto every onsen record that "
            "already has a place_id. With no arguments, processes every "
            "*_springs.jsonl in backend/data/."
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
        help="Only resolve the first N records missing a rating, per file (for testing).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report how many records need a rating, without calling the API or writing.",
    )
    return parser.parse_args()


def backfill_file(path: Path, limit: int | None, dry_run: bool) -> dict:
    """Resolve + write back rating data for records in one jsonl file."""
    records = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    no_place_id = sum(1 for r in records if not r.get("place_id"))
    already_rated = sum(1 for r in records if r.get("place_id") and r.get("rating") is not None)
    to_process = [
        r for r in records if r.get("place_id") and r.get("rating") is None
    ]
    if limit is not None:
        to_process = to_process[:limit]

    print(
        f"[backfill_place_ratings] {path.name}: {len(records)} records total, "
        f"{no_place_id} without a place_id (skipped), "
        f"{already_rated} already rated, {len(to_process)} to resolve this run"
    )

    if dry_run:
        return {"resolved": 0, "failed": 0, "skipped": already_rated + no_place_id}

    resolved = 0
    failed = 0
    for record in to_process:
        try:
            result = get_place_rating(record["place_id"])
            record["rating"] = result["rating"]
            record["user_rating_count"] = result["user_rating_count"]
            record["review_summary"] = result["review_summary"]
            record["review_summary_disclosure"] = result["review_summary_disclosure"]
            resolved += 1
        except PlacesError as exc:
            print(f"[backfill_place_ratings]   FAILED: {record.get('name')} — {exc}")
            failed += 1

    if resolved:
        # Atomic write: build the full file in a temp path, then replace —
        # a crash mid-run leaves the original file untouched.
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        tmp_path.replace(path)
        print(f"[backfill_place_ratings]   wrote {resolved} new rating(s) back to {path.name}")

    return {"resolved": resolved, "failed": failed, "skipped": already_rated + no_place_id}


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
        print("\n[backfill_place_ratings] ERROR — the following slugs could not be resolved:\n")
        for err in errors:
            print(f"  {err}")
        sys.exit(1)

    print(f"[backfill_place_ratings] Processing {len(data_files)} file(s): "
          f"{', '.join(f.name for f in data_files)}\n")

    totals = {"resolved": 0, "failed": 0, "skipped": 0}
    for data_file in data_files:
        result = backfill_file(data_file, args.limit, args.dry_run)
        for key in totals:
            totals[key] += result[key]
        print()

    print(
        f"[backfill_place_ratings] Done. resolved={totals['resolved']} "
        f"failed={totals['failed']} skipped={totals['skipped']}"
    )
