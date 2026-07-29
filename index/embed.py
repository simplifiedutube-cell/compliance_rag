"""Embedding providers.

Four options, in the order you should prefer them for a demo:

  local   fastembed + BAAI/bge-small-en-v1.5. No API key, no network, no quota.
          ~65 MB model, 384 dims, embeds the whole 944-chunk corpus in well
          under a minute on a laptop CPU. For a compliance prospect this is
          also the strongest possible privacy answer: nothing leaves the
          machine.
  voyage  voyage-4-lite. 200M free tokens on signup -- ~300x more than this
          project needs. Best hosted quality. Their data-use position is not
          published, so don't point it at a prospect's real documents until
          you have that in writing.
  gemini  gemini-embedding-001, free of charge. BUT Google's terms say
          free-tier input and output are used to improve their products and
          "human reviewers may read" them. Fine for this demo's synthetic and
          public data; never for a client's documents.
  openai  text-embedding-3-small. No free tier -- $5 minimum credit purchase.

Set EMBED_PROVIDER in .env.
"""
from __future__ import annotations

import time

import numpy as np

import config


class EmbeddingError(RuntimeError):
    pass


# --------------------------------------------------------------- local (free)

_LOCAL_MODEL = None

# bge models are trained with an asymmetric prefix on the query side only.
# Omitting it measurably degrades retrieval.
_BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def _local(texts: list[str], query: bool) -> np.ndarray:
    global _LOCAL_MODEL
    try:
        from fastembed import TextEmbedding
    except ImportError as e:
        raise EmbeddingError(
            "fastembed is not installed. Run: pip install fastembed") from e

    if _LOCAL_MODEL is None:
        # First call downloads ~65 MB and caches it under ~/.cache/fastembed.
        _LOCAL_MODEL = TextEmbedding(model_name=config.LOCAL_EMBED_MODEL)

    if query and "bge" in config.LOCAL_EMBED_MODEL.lower():
        texts = [_BGE_QUERY_PREFIX + t for t in texts]

    # Memory, not speed, is the constraint here. fastembed defaults to
    # batch_size=256 and data-parallel workers; on ~900-token legal chunks
    # that peaked at 7.7 GB RSS and was OOM-killed on an 8 GB machine.
    # Small batches + single-threaded keeps it flat at a few hundred MB.
    #
    # Progress matters too: on a slow CPU this takes minutes, and a silent
    # build looks like a hang.
    # Single query (or a handful): take the plain path. Passing `parallel`
    # makes fastembed spin up multiprocessing workers, which fails inside a
    # uvicorn worker thread on macOS -- the build works and every query then
    # silently degrades to lexical-only. Only batch-embed at index time.
    if len(texts) <= 8:
        return np.asarray(list(_LOCAL_MODEL.embed(texts)), dtype=np.float32)

    show = len(texts) > 50
    out: list[np.ndarray] = []
    step = config.LOCAL_EMBED_BATCH
    t0 = time.time()
    for i in range(0, len(texts), step):
        out.extend(_LOCAL_MODEL.embed(texts[i:i + step], batch_size=step))
        if show:
            done = min(i + step, len(texts))
            rate = done / max(time.time() - t0, 1e-6)
            eta = (len(texts) - done) / max(rate, 1e-6)
            print(f"    embedded {done}/{len(texts)}  "
                  f"({rate:.0f}/s, ~{eta:.0f}s left)", end="\r", flush=True)
    if show:
        print(f"    embedded {len(texts)}/{len(texts)} in "
              f"{time.time() - t0:.0f}s" + " " * 24, flush=True)
    return np.asarray(out, dtype=np.float32)


# -------------------------------------------------------------------- hosted

def _openai(texts: list[str]) -> np.ndarray:
    from openai import OpenAI
    if not config.OPENAI_API_KEY:
        raise EmbeddingError("OPENAI_API_KEY is not set")
    client = OpenAI(api_key=config.OPENAI_API_KEY)
    vecs: list[list[float]] = []
    for i in range(0, len(texts), 128):
        r = client.embeddings.create(model=config.OPENAI_EMBED_MODEL,
                                     input=texts[i:i + 128])
        vecs.extend(d.embedding for d in r.data)
    return np.asarray(vecs, dtype=np.float32)


class _RateLimiter:
    """Rolling-window limiter for requests/min and tokens/min.

    Voyage's free tier without a payment method is 3 RPM / 10K TPM. A fixed
    batch of 64 legal chunks is ~12K tokens, which busts the token cap in a
    single request -- so batches are sized by token budget, not count.
    """

    def __init__(self, rpm: int, tpm: int) -> None:
        self.rpm, self.tpm = rpm, tpm
        self.events: list[tuple[float, int]] = []   # (timestamp, tokens)

    def _prune(self, now: float) -> None:
        self.events = [(t, n) for t, n in self.events if now - t < 60.0]

    def wait(self, tokens: int) -> None:
        while True:
            now = time.time()
            self._prune(now)
            reqs = len(self.events)
            toks = sum(n for _, n in self.events)
            if reqs < self.rpm and toks + tokens <= self.tpm:
                self.events.append((now, tokens))
                return
            oldest = min(t for t, _ in self.events)
            time.sleep(max(60.0 - (now - oldest), 0.5) + 0.25)


def _voyage(texts: list[str], input_type: str) -> np.ndarray:
    import voyageai
    if not config.VOYAGE_API_KEY:
        raise EmbeddingError("VOYAGE_API_KEY is not set")

    client = voyageai.Client(api_key=config.VOYAGE_API_KEY)
    limiter = _RateLimiter(config.VOYAGE_RPM, config.VOYAGE_TPM)

    # Keep each request under 80% of the per-minute token cap so one batch
    # can never be individually unservable.
    budget = max(int(config.VOYAGE_TPM * 0.8), 1000)
    est = [int(len(t.split()) * 1.4) + 8 for t in texts]

    batches: list[list[int]] = []
    cur: list[int] = []
    cur_tok = 0
    for i, n in enumerate(est):
        if cur and (cur_tok + n > budget or len(cur) >= 128):
            batches.append(cur)
            cur, cur_tok = [], 0
        cur.append(i)
        cur_tok += n
    if cur:
        batches.append(cur)

    total_tok = sum(est)
    show = len(texts) > 50
    if show:
        mins = total_tok / max(config.VOYAGE_TPM, 1)
        print(f"    voyage: {len(texts)} texts, ~{total_tok:,} tokens, "
              f"{len(batches)} batches")
        print(f"    free-tier limits {config.VOYAGE_RPM} RPM / "
              f"{config.VOYAGE_TPM:,} TPM  ->  ~{mins:.0f} min minimum",
              flush=True)

    vecs: list[list[float]] = [None] * len(texts)     # type: ignore[list-item]
    t0 = time.time()
    for bi, idxs in enumerate(batches, 1):
        batch = [texts[i] for i in idxs]
        tok = sum(est[i] for i in idxs)
        limiter.wait(tok)

        for attempt in range(6):
            try:
                r = client.embed(batch, model=config.VOYAGE_MODEL,
                                 input_type=input_type)
                for j, emb in zip(idxs, r.embeddings):
                    vecs[j] = emb
                break
            except Exception as e:                    # noqa: BLE001
                if "rate" not in str(e).lower() and "429" not in str(e):
                    raise
                back = min(2 ** attempt * 5, 65)
                if show:
                    print(f"    rate limited, backing off {back}s "
                          f"(attempt {attempt + 1}/6)" + " " * 12,
                          flush=True)
                time.sleep(back)
        else:
            raise EmbeddingError(
                "Voyage rate limit persisted after 6 retries. Either add a "
                "payment method at https://dashboard.voyageai.com/ (free "
                "tokens still apply), or set EMBED_PROVIDER=local.")

        if show:
            done = sum(len(b) for b in batches[:bi])
            el = time.time() - t0
            eta = el / bi * (len(batches) - bi)
            print(f"    embedded {done}/{len(texts)}  batch {bi}/{len(batches)}"
                  f"  ~{eta / 60:.1f} min left" + " " * 10,
                  end="\r", flush=True)
    if show:
        print(f"    embedded {len(texts)}/{len(texts)} in "
              f"{(time.time() - t0) / 60:.1f} min" + " " * 24, flush=True)

    return np.asarray(vecs, dtype=np.float32)


def _gemini(texts: list[str], query: bool) -> np.ndarray:
    from google import genai
    from google.genai import types
    if not config.GEMINI_API_KEY:
        raise EmbeddingError("GEMINI_API_KEY is not set")
    client = genai.Client(api_key=config.GEMINI_API_KEY)
    task = "RETRIEVAL_QUERY" if query else "RETRIEVAL_DOCUMENT"
    vecs: list[list[float]] = []
    for i in range(0, len(texts), 100):
        r = client.models.embed_content(
            model=config.GEMINI_EMBED_MODEL,
            contents=texts[i:i + 100],
            config=types.EmbedContentConfig(task_type=task),
        )
        vecs.extend(e.values for e in r.embeddings)
    return np.asarray(vecs, dtype=np.float32)


# ---------------------------------------------------------------------- api

def provider_name() -> str:
    p = config.EMBED_PROVIDER
    return f"{p} ({config.LOCAL_EMBED_MODEL})" if p == "local" else p


def embed(texts: list[str], *, query: bool = False) -> np.ndarray:
    """Return L2-normalised embeddings, so cosine similarity is a dot product."""
    if not texts:
        return np.zeros((0, 1), dtype=np.float32)

    p = config.EMBED_PROVIDER
    if p == "local":
        v = _local(texts, query)
    elif p == "voyage":
        v = _voyage(texts, "query" if query else "document")
    elif p == "gemini":
        v = _gemini(texts, query)
    elif p == "openai":
        v = _openai(texts)
    else:
        raise EmbeddingError(f"Unknown EMBED_PROVIDER: {p}")

    norms = np.linalg.norm(v, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return v / norms
