"""Diagnose why retrieval is degrading to lexical-only.

    python scripts/diagnose_retrieval.py

Runs one query end to end and prints exactly where dense retrieval breaks:
provider config, index dimension, query dimension, and the raw exception if
there is one. Prints the fix for each known cause.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json                                                        # noqa: E402

import config                                                      # noqa: E402
from index import embed as embedder                                # noqa: E402
from index import store                                            # noqa: E402

Q = "when must a threshold transaction report be given to AUSTRAC"


def main() -> int:
    print("\n--- config ---")
    print(f"  EMBED_PROVIDER = {config.EMBED_PROVIDER}")
    if config.EMBED_PROVIDER == "local":
        print(f"  LOCAL_EMBED_MODEL = {config.LOCAL_EMBED_MODEL}")
    print(f"  reranker = "
          f"{config.COHERE_RERANK_MODEL if config.COHERE_API_KEY else 'off'}")

    print("\n--- index on disk ---")
    try:
        vectors = store.load_vectors()
        print(f"  vectors.npy : {vectors.shape[0]} rows, "
              f"dim {vectors.shape[1]}")
    except Exception as e:                            # noqa: BLE001
        print(f"  FAILED to load vectors: {e}")
        print("\n  FIX: python -m index.build")
        return 1

    st = store.stats()
    print(f"  index.db    : {st['total']} chunks")
    if st["total"] != vectors.shape[0]:
        print(f"  !! MISMATCH: {st['total']} chunks but "
              f"{vectors.shape[0]} vectors")
        print("\n  FIX: python -m index.build")
        return 1

    if config.BUILD_MANIFEST.exists():
        m = json.loads(config.BUILD_MANIFEST.read_text())
        print(f"  built with  : {m.get('embed_provider')} "
              f"(dim {m.get('embed_dim', '?')}) at {m.get('built_at')}")

    print("\n--- query-time embedding ---")
    try:
        qv = embedder.embed([Q], query=True)[0]
        print(f"  OK: {config.EMBED_PROVIDER} produced dim {qv.shape[0]}")
    except Exception as e:                            # noqa: BLE001
        print(f"  FAILED: {type(e).__name__}: {e}")
        print("\n  This is why retrieval is degrading to lexical-only.")
        if "fastembed" in str(e).lower():
            print("  FIX: pip install fastembed")
        elif "api" in str(e).lower() or "key" in str(e).lower():
            print(f"  FIX: check the key for EMBED_PROVIDER="
                  f"{config.EMBED_PROVIDER} in .env")
        return 1

    if qv.shape[0] != vectors.shape[1]:
        print(f"\n  !! DIMENSION MISMATCH")
        print(f"     index was built at dim {vectors.shape[1]}")
        print(f"     EMBED_PROVIDER={config.EMBED_PROVIDER} now gives "
              f"dim {qv.shape[0]}")
        print(f"\n  This is the cause. The index and the query are using "
              f"different\n  embedding models, so the dot product is invalid "
              f"and dense retrieval\n  is skipped.")
        print(f"\n  FIX (either one):")
        print(f"    - rebuild with the current provider: python -m index.build")
        print(f"    - or set EMBED_PROVIDER back to whatever built the index")
        return 1

    print("\n--- live retrieval ---")
    from query.retrieve import retrieve
    hits, diag = retrieve(Q, tenant_id="meridian",
                          segment=config.DEMO_SEGMENT)
    print(f"  dense candidates   : {diag.get('dense', 0)}")
    print(f"  lexical candidates : {diag.get('lexical', 0)}")
    print(f"  reranked           : {diag.get('reranked')}")
    if diag.get("dense_error"):
        print(f"  DENSE ERROR: {diag['dense_error']}")
    if diag.get("rerank_error"):
        print(f"  RERANK ERROR: {diag['rerank_error']}")
    print(f"\n  top hits:")
    for h in hits[:5]:
        print(f"    {h.score:7.4f}  {h.why:14s} {h.citation_key[:46]}")

    ok = diag.get("dense", 0) > 0 and not diag.get("dense_error")
    print()
    if ok:
        print("Dense retrieval is working. If answers still refuse, the "
              "cause is\nelsewhere — check MIN_RERANK_SCORE and the "
              "Evaluation tab.")
        return 0
    print("Dense retrieval is NOT contributing. Fix the error above.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
