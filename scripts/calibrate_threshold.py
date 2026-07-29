"""Calibrate MIN_RERANK_SCORE from data instead of guessing it.

    python scripts/calibrate_threshold.py

Rerank scores are not comparable across models. On rerank-english-v3.0 a
genuinely relevant statutory passage can score 0.04; on another model the same
passage scores 0.9. Picking a threshold by intuition therefore produces one of
two failures:

  too high -> the assistant refuses almost everything and gets switched off
  too low  -> the low-confidence refusal never fires and it guesses instead

This script runs the golden set, records the top reranked score for every
question, and reports the separation between questions that SHOULD be
answerable and questions that are out of corpus. If the two populations don't
separate, it says so rather than inventing a number.
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config                                                      # noqa: E402
from query.retrieve import retrieve                                # noqa: E402

GOLDEN = Path(__file__).parent.parent / "eval" / "golden.yaml"

# Cases whose whole point is that the corpus cannot answer them.
OUT_OF_CORPUS_IDS = {"R03", "R04", "E02"}


def main() -> int:
    if not config.COHERE_API_KEY:
        print("No reranker configured — MIN_RERANK_SCORE does nothing.\n"
              "Set COHERE_API_KEY first, or leave the threshold at 0.")
        return 0

    cases = yaml.safe_load(GOLDEN.read_text())
    answerable: list[tuple[str, float]] = []
    out_of_corpus: list[tuple[str, float]] = []

    print(f"Scoring {len(cases)} golden-set questions "
          f"through {config.COHERE_RERANK_MODEL}\n")

    for c in cases:
        hits, _ = retrieve(c["question"], tenant_id=c.get("tenant"),
                           segment=config.DEMO_SEGMENT)
        top = hits[0].score if hits else 0.0
        bucket = out_of_corpus if c["id"] in OUT_OF_CORPUS_IDS else answerable
        bucket.append((c["id"], top))
        tag = "out-of-corpus" if c["id"] in OUT_OF_CORPUS_IDS else "answerable"
        print(f"  {c['id']:5s} {top:7.4f}  {tag:14s} {c['question'][:52]}")

    a = sorted(s for _, s in answerable)
    o = sorted(s for _, s in out_of_corpus)

    print("\n" + "=" * 66)
    print(f"Answerable questions   n={len(a):2d}  "
          f"min {a[0]:.4f}  median {statistics.median(a):.4f}  max {a[-1]:.4f}")
    if o:
        print(f"Out-of-corpus          n={len(o):2d}  "
              f"min {o[0]:.4f}  median {statistics.median(o):.4f}  "
              f"max {o[-1]:.4f}")

    print()
    if not o:
        print("No out-of-corpus cases scored — cannot calibrate. Leave "
              "MIN_RERANK_SCORE=0.")
        return 0

    lowest_answerable = a[0]
    highest_junk = o[-1]

    if highest_junk < lowest_answerable:
        # Clean separation: put the threshold in the gap, nearer the junk side
        # so a borderline real question is answered rather than refused.
        rec = highest_junk + (lowest_answerable - highest_junk) * 0.35
        print(f"Populations separate cleanly.")
        print(f"  highest out-of-corpus  {highest_junk:.4f}")
        print(f"  lowest answerable      {lowest_answerable:.4f}")
        print(f"\n  RECOMMEND  MIN_RERANK_SCORE={rec:.3f}")
        print("\nSet it in .env, then re-run: python -m eval.run_eval")
        print("Check the refusal rate afterwards — target ≤ 20%.")
    else:
        # Overlap: any threshold trades a missed refusal for an over-refusal.
        safe = max(0.0, lowest_answerable * 0.5)
        print("Populations OVERLAP — the reranker score alone cannot "
              "separate answerable from out-of-corpus questions on this "
              "corpus.")
        print(f"  lowest answerable      {lowest_answerable:.4f}")
        print(f"  highest out-of-corpus  {highest_junk:.4f}")
        print(f"\n  RECOMMEND  MIN_RERANK_SCORE=0   (leave it off)")
        print(f"  A conservative floor would be {safe:.3f}, but it buys "
              f"little:\n  the hard refusal rules (entity scoping, legal "
              f"advice, out-of-jurisdiction,\n  uncited deadlines) already "
              f"catch these cases, and they do it\n  deterministically rather "
              f"than by a score.")
        print("\nThis is a real result, not a failure. Score thresholds are a "
              "weak\nsignal in a narrow corpus where everything is topically "
              "similar.")

    out = {
        "model": config.COHERE_RERANK_MODEL,
        "answerable": answerable,
        "out_of_corpus": out_of_corpus,
    }
    (config.DATA / "rerank_calibration.json").write_text(json.dumps(out, indent=2))
    print(f"\nWrote {config.DATA / 'rerank_calibration.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
