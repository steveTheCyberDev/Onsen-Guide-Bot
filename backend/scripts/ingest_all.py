"""Combined one-off ingest entrypoint for deploys (the Railway post-deploy job).

Runs BOTH ingests, in order, so the Layer 2 knowledge base is never forgotten
alongside the onsen data. That failure mode is *silent*: ask mode would fall
back to "I don't have that information" against an empty KB collection while
search/recommend look fine. Running them together makes the deploy "ingest
gate" a single command.

    python -m scripts.ingest_all

Both sub-ingests are idempotent (Chroma ``upsert`` with deterministic ids), so
re-running on every deploy is safe. Each reads the SAME settings the app reads
(``chroma_path``, ``data_dir``, ``kb_data_dir``), so the app and the ingest job
never disagree — the single-source-of-truth config pattern.

Run this BEFORE flipping ``ASK_ENABLED`` in prod (ingest-first-then-flip).
"""

from __future__ import annotations

import subprocess
import sys

# Order: onsen regions first (the core dataset), then the Layer 2 KB prose.
INGEST_MODULES = ["scripts.ingest_regions", "scripts.ingest_knowledge"]


def main() -> None:
    for module in INGEST_MODULES:
        print(f"\n=== ingest_all: running {module} ===", flush=True)
        subprocess.run([sys.executable, "-m", module], check=True)
    print("\n=== ingest_all: all ingests complete ===", flush=True)


if __name__ == "__main__":
    main()
