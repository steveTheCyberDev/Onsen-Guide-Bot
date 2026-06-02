"""
Subset-aware ingest wrapper for Onsen Guide Bot.

Calls the existing ingest() function from ingest.py — no ingest logic lives here.

Usage (run from the backend/ directory so that the sys.path setup in ingest.py
works correctly, or from the project root as shown below):

  # Ingest only the launch subset (okinawa + tokai):
  python backend/scripts/ingest_regions.py

  # Ingest specific slugs:
  python backend/scripts/ingest_regions.py --regions okinawa tokai kanto

  # Ingest every *_springs.jsonl in backend/data/:
  python backend/scripts/ingest_regions.py --all

  # Pass a custom batch size through to ingest():
  python backend/scripts/ingest_regions.py --batch-size 10

Runtime note:
  Importing ingest.py triggers module-level code that constructs an OpenAI client
  via settings.openai_api_key, so all env vars must be present at runtime.
  On Railway this is satisfied by the Railway env panel.  Locally, backend/.env
  must exist.  The wrapper itself is safe to import without triggering any ingest.

Safe-to-import guarantee:
  All network I/O and ingest calls are guarded by `if __name__ == "__main__"`.
"""

import argparse
import sys
from pathlib import Path

# ── Path setup ───────────────────────────────────────────────────────────────
# Keep identical to the pattern in ingest.py so both scripts resolve the same
# BACKEND_DIR and DATA_DIR when run from any working directory.

BACKEND_DIR = Path(__file__).parent.parent       # .../backend
DATA_DIR = BACKEND_DIR / "data"

# ── Launch subset ─────────────────────────────────────────────────────────────
# The current launch subset — add slugs here to expand coverage.
# Full slug list: okinawa, tokai, kanto, kinki, chugoku, shikoku,
#                 kyushu, hokkaido, hokuriku, tohoku
ACTIVE_REGIONS: list[str] = [
    "okinawa",  # ~3 records
    "tokai",    # ~217 records
]

# ── Known slugs ───────────────────────────────────────────────────────────────
# These are the only slugs with a *_springs.jsonl file.  Anything outside this
# set will produce a clear error rather than a silent no-op.
ALL_KNOWN_SLUGS: list[str] = [
    "okinawa",
    "tokai",
    "kanto",
    "kinki",
    "chugoku",
    "shikoku",
    "kyushu",
    "hokkaido",
    "hokuriku",
    "tohoku",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def resolve_data_file(slug: str) -> Path:
    """Return the Path for a given slug, or raise with a clear message."""
    path = DATA_DIR / f"{slug}_springs.jsonl"
    if not path.exists():
        raise FileNotFoundError(
            f"No data file found for slug '{slug}': expected {path}\n"
            f"  Valid slugs: {', '.join(ALL_KNOWN_SLUGS)}\n"
            f"  Note: regions.jsonl and dropdown_options.json use a different schema "
            f"and must NOT be ingested via this script."
        )
    return path


def discover_all_slugs() -> list[str]:
    """Return slugs for every *_springs.jsonl present in DATA_DIR."""
    return sorted(
        p.stem.replace("_springs", "")
        for p in DATA_DIR.glob("*_springs.jsonl")
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Ingest one or more onsen regions into ChromaDB.\n"
            "With no arguments, ingests the ACTIVE_REGIONS launch subset "
            f"({', '.join(ACTIVE_REGIONS)})."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    target_group = parser.add_mutually_exclusive_group()
    target_group.add_argument(
        "--regions",
        nargs="+",
        metavar="SLUG",
        help=(
            "Space-separated list of region slugs to ingest "
            f"(e.g. okinawa tokai kanto).  Valid slugs: {', '.join(ALL_KNOWN_SLUGS)}"
        ),
    )
    target_group.add_argument(
        "--all",
        action="store_true",
        help="Ingest every *_springs.jsonl found in backend/data/.",
    )

    parser.add_argument(
        "--batch-size",
        type=int,
        default=20,
        metavar="N",
        help="Number of records per translation batch (default: 20).",
    )

    return parser.parse_args()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = parse_args()

    # Determine which slugs to process
    if args.all:
        slugs = discover_all_slugs()
        print(f"[ingest_regions] --all: discovered {len(slugs)} region(s): {', '.join(slugs)}")
    elif args.regions:
        slugs = args.regions
        print(f"[ingest_regions] --regions: processing {len(slugs)} region(s): {', '.join(slugs)}")
    else:
        slugs = list(ACTIVE_REGIONS)
        print(
            f"[ingest_regions] No flags — using ACTIVE_REGIONS launch subset: "
            f"{', '.join(slugs)}"
        )

    # Validate all slugs before importing ingest (which costs an OpenAI client init)
    data_files: list[Path] = []
    errors: list[str] = []
    for slug in slugs:
        try:
            data_files.append(resolve_data_file(slug))
        except FileNotFoundError as exc:
            errors.append(str(exc))

    if errors:
        print("\n[ingest_regions] ERROR — the following slugs could not be resolved:\n")
        for err in errors:
            print(f"  {err}\n")
        sys.exit(1)

    print(f"\n[ingest_regions] Will ingest {len(data_files)} file(s):")
    for f in data_files:
        print(f"  {f}")
    print()

    # Import ingest only now (triggers OpenAI client construction — needs env vars)
    sys.path.insert(0, str(BACKEND_DIR))
    from scripts.ingest import ingest  # noqa: E402

    total_before = None
    for data_file in data_files:
        print(f"[ingest_regions] ── Ingesting: {data_file.name} ──")
        ingest(data_file, batch_size=args.batch_size)
        print()

    print(
        f"[ingest_regions] Finished. {len(data_files)} region(s) ingested: "
        f"{', '.join(slugs)}"
    )
    if len(slugs) < len(ALL_KNOWN_SLUGS):
        deferred = [s for s in ALL_KNOWN_SLUGS if s not in slugs]
        print(
            f"[ingest_regions] Deferred regions (run with --all or --regions to add): "
            f"{', '.join(deferred)}"
        )
