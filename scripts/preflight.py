"""Preflight: verify every configured provider actually works.

    python scripts/preflight.py

Makes one tiny call per provider so a bad key surfaces in seconds rather
than eight minutes into a build. Exits non-zero if anything required for the
current configuration is broken.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config                                                      # noqa: E402

OK, FAIL, SKIP, WARN = "  OK  ", " FAIL ", " skip ", " WARN "
results: list[tuple[str, str, str]] = []


def _clean_error(e: Exception) -> str:
    """Provider SDKs love dumping raw headers. Keep the useful part."""
    msg = str(e)
    for marker in ("'message': '", '"message": "'):
        if marker in msg:
            rest = msg.split(marker, 1)[1]
            msg = rest.split("'", 1)[0] if marker.endswith("'") \
                else rest.split('"', 1)[0]
            break
    else:
        if "headers:" in msg:
            msg = msg.split("headers:")[0].strip() or type(e).__name__
    return f"{type(e).__name__}: {msg}"[:180]


def check(name: str, fn, *, required: bool = True) -> None:
    t0 = time.time()
    try:
        detail = fn()
        results.append((OK, name, f"{detail}  ({time.time()-t0:.1f}s)"))
    except Exception as e:                            # noqa: BLE001
        # A broken optional provider is worth knowing about, but it must not
        # block the build -- the pipeline degrades without it.
        results.append((FAIL if required else WARN, name, _clean_error(e)))


def skip(name: str, why: str) -> None:
    results.append((SKIP, name, why))


def warn(name: str, why: str) -> None:
    results.append((WARN, name, why))


# --------------------------------------------------------------- embeddings

def _voyage():
    import voyageai
    c = voyageai.Client(api_key=config.VOYAGE_API_KEY)
    r = c.embed(["threshold transaction report"], model=config.VOYAGE_MODEL,
                input_type="document")
    return f"{config.VOYAGE_MODEL}, dim {len(r.embeddings[0])}"


def _local():
    from fastembed import TextEmbedding
    m = TextEmbedding(model_name=config.LOCAL_EMBED_MODEL)
    v = list(m.embed(["threshold transaction report"]))
    return f"{config.LOCAL_EMBED_MODEL}, dim {len(v[0])}"


def _gemini_embed():
    from google import genai
    from google.genai import types
    c = genai.Client(api_key=config.GEMINI_API_KEY)
    r = c.models.embed_content(
        model=config.GEMINI_EMBED_MODEL, contents=["test"],
        config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"))
    return f"{config.GEMINI_EMBED_MODEL}, dim {len(r.embeddings[0].values)}"


def _openai_embed():
    from openai import OpenAI
    c = OpenAI(api_key=config.OPENAI_API_KEY)
    r = c.embeddings.create(model=config.OPENAI_EMBED_MODEL, input=["test"])
    return f"{config.OPENAI_EMBED_MODEL}, dim {len(r.data[0].embedding)}"


EMBED = {"voyage": _voyage, "local": _local,
         "gemini": _gemini_embed, "openai": _openai_embed}

# --------------------------------------------------------------- generation

def _generation():
    from query import llm
    out = llm.generate_json(
        'Reply with JSON only: {"ok": true}',
        'Say ok.', max_tokens=64)
    return f"{llm.provider_label()} → {out}"


# ----------------------------------------------------------------- reranker

def _cohere():
    """Check the reranker AND show the score spread.

    Rerank scores are not calibrated across models -- a genuinely relevant
    document can score 0.04 on one model and 0.9 on another. What matters is
    the gap between relevant and irrelevant, not the absolute value. Printing
    both is how you find out your refusal threshold is wrong before a
    prospect does.
    """
    import cohere
    c = cohere.ClientV2(config.COHERE_API_KEY)
    relevant = ("A threshold transaction report must be given to the AUSTRAC "
                "CEO within 10 business days after the transaction.")
    irrelevant = ("The office kitchen roster is updated each Monday by the "
                  "practice manager.")
    r = c.rerank(model=config.COHERE_RERANK_MODEL,
                 query="when must a threshold transaction report be given",
                 documents=[relevant, irrelevant], top_n=2)
    scores = {res.index: res.relevance_score for res in r.results}
    hi, lo = scores.get(0, 0.0), scores.get(1, 0.0)
    ordered = "correct doc ranked first" if r.results[0].index == 0 \
        else "WRONG ORDER -- irrelevant doc ranked first"
    note = ""
    if hi < config.MIN_RERANK_SCORE:
        note = (f"\n           !! relevant doc scores {hi:.3f} but "
                f"MIN_RERANK_SCORE={config.MIN_RERANK_SCORE} -- this "
                f"threshold would refuse it.\n           "
                f"Run: python scripts/calibrate_threshold.py")
    return (f"{config.COHERE_RERANK_MODEL} | relevant {hi:.3f} vs "
            f"irrelevant {lo:.3f} | {ordered}{note}")


# -------------------------------------------------------------------- index

def _index():
    from index import store
    st = store.stats()
    if not st.get("total"):
        raise RuntimeError("index is empty — run: python -m index.build")
    return (f"{st['total']} chunks, tenants: "
            f"{', '.join(st.get('tenants') or ['none'])}")


def main() -> int:
    print(f"\nConfiguration")
    print(f"  EMBED_PROVIDER = {config.EMBED_PROVIDER}")
    print(f"  LLM_PROVIDER   = {config.LLM_PROVIDER}")
    print(f"  reranker       = "
          f"{'cohere ' + config.COHERE_RERANK_MODEL if config.COHERE_API_KEY else 'off'}")
    print()

    # Embeddings — the active provider is required, others are informational.
    active = config.EMBED_PROVIDER
    if active not in EMBED:
        results.append((FAIL, f"embeddings [{active}]",
                        f"unknown provider; use one of {sorted(EMBED)}"))
    else:
        check(f"embeddings [{active}] REQUIRED", EMBED[active])

    if active == "voyage":
        warn("voyage free-tier throttle",
             f"Without a payment method on file Voyage allows "
             f"{config.VOYAGE_RPM} req/min and {config.VOYAGE_TPM:,} "
             f"tokens/min. This corpus is ~179k tokens, so a full build "
             f"takes ~18 minutes. The 200M free tokens are not the "
             f"constraint -- the throttle is. Add a card (free tokens still "
             f"apply) or use EMBED_PROVIDER=local.")

    # Any other configured embedding provider, so you can switch confidently.
    if active != "voyage" and config.VOYAGE_API_KEY:
        check("embeddings [voyage] (alt)", _voyage, required=False)
    if active != "local":
        check("embeddings [local] (alt, no key)", _local, required=False)

    # Generation
    from query import llm
    if llm.available():
        check(f"generation [{config.LLM_PROVIDER}] REQUIRED", _generation)
    else:
        results.append((FAIL, f"generation [{config.LLM_PROVIDER}]",
                        "no key configured — chat will return retrieved "
                        "sources without a generated answer"))

    # Reranker — optional
    if config.COHERE_API_KEY:
        check("reranker [cohere] optional", _cohere, required=False)
        warn("reranker trial limits",
             "Cohere trial keys: rerank 10 req/min, 1,000 API calls/month "
             "total. Trial data may be used by Cohere — see their terms "
             "before pointing it at a client's documents.")
        if config.MIN_RERANK_SCORE > 0:
            warn("refusal threshold now live",
                 f"MIN_RERANK_SCORE={config.MIN_RERANK_SCORE} only takes "
                 f"effect once a reranker is set. Calibrate it against the "
                 f"golden set rather than guessing: "
                 f"python scripts/calibrate_threshold.py")
    else:
        skip("reranker [cohere]", "not configured — retrieval falls back to "
                                  "RRF order (works, just less precise)")

    # Index
    check("index", _index)

    print("Checks")
    for status, name, detail in results:
        print(f"  [{status}] {name}\n           {detail}")

    failed = [r for r in results if r[0] == FAIL]
    warned = [r for r in results if r[0] == WARN]
    print()
    if failed:
        print(f"{len(failed)} REQUIRED check(s) failed. Fix these before "
              f"building.\n")
        return 1
    if warned:
        print(f"{len(warned)} warning(s) above — none block the build.\n")
    print("All required checks passed. Next:\n"
          "  python -m index.build          # build the index\n"
          "  python -m eval.run_eval        # measure it\n"
          "  uvicorn app.main:app --port 8000\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
