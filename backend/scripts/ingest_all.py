"""Combined one-off ingest entrypoint for deploys (the Railway post-deploy job).

Runs BOTH ingests, in order, so the Layer 2 knowledge base is never forgotten
alongside the onsen data. That failure mode is *silent*: ask mode would fall
back to "I don't have that information" against an empty KB collection while
search/recommend look fine. Running them together makes the deploy "ingest
gate" a single command.

    python -m scripts.ingest_all
    python -m scripts.ingest_all --all           # every region, not just the
                                                  # ACTIVE_REGIONS launch subset
    python -m scripts.ingest_all --regions kanto kinki

Any CLI args are forwarded VERBATIM to ``scripts.ingest_regions`` only (its
``--all``/``--regions``/``--batch-size`` flags) — ``scripts.ingest_knowledge``
takes an unrelated ``--dir`` override and is always called bare, since there is
no knowledge-base analogue of "which regions". Previously this script silently
ignored every CLI arg (no ``sys.argv`` read at all), so `ingest_all --all`
always ran the bare 3-region ``ACTIVE_REGIONS`` subset no matter what was
passed — a real gap, not a doc/usage mistake.

Both sub-ingests are idempotent (Chroma ``upsert`` with deterministic ids), so
re-running on every deploy is safe. Each reads the SAME settings the app reads
(``chroma_path``, ``data_dir``, ``kb_data_dir``), so the app and the ingest job
never disagree — the single-source-of-truth config pattern.

Run this BEFORE flipping ``ASK_ENABLED`` in prod (ingest-first-then-flip).
"""

from __future__ import annotations

import subprocess
import sys


def main(region_args: list[str] | None = None) -> None:
    # ``region_args`` defaults to None (not read here) rather than reaching for
    # sys.argv directly, so tests can call main() or main([...]) deterministically
    # without picking up the CALLING process's own argv (e.g. pytest's).
    if region_args is None:
        region_args = []

    print(f"\n=== ingest_all: running scripts.ingest_regions {' '.join(region_args)} ===", flush=True)
    subprocess.run(
        [sys.executable, "-m", "scripts.ingest_regions", *region_args], check=True
    )

    print("\n=== ingest_all: running scripts.ingest_knowledge ===", flush=True)
    subprocess.run([sys.executable, "-m", "scripts.ingest_knowledge"], check=True)

    print("\n=== ingest_all: all ingests complete ===", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
