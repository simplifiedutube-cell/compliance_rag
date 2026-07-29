"""Hybrid retrieval: dense + BM25, fused with RRF, then reranked.

Dense alone fails on this corpus in two specific ways:
  * terms of art -- "suspicious matter" and "suspicious transaction" sit
    almost on top of each other in embedding space but are different
    reporting obligations with different deadlines;
  * identifiers -- "section 36" and "section 43" embed to nearly the same
    point, so "what does section 36 require?" retrieves noise.

BM25 handles both. Hence hybrid is mandatory here, not an optimisation.
"""
from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass

import numpy as np

import config
from index import embed as embedder
from index import store

_SECTION_Q = re.compile(r"\bs(?:ec(?:tion)?)?\.?\s*(\d+[A-Z]{0,3})\b", re.I)

# Statutory terms of art worth pulling definitions for whenever they appear.
KEY_TERMS = [
    "designated service", "reporting entity", "threshold transaction",
    "suspicious matter", "customer due diligence", "beneficial owner",
    "precious metal", "precious stone", "virtual asset", "AML/CTF program",
    "politically exposed person", "money laundering", "terrorism financing",
    "compliance officer", "ongoing customer due diligence",
]


@dataclass
class Hit:
    citation_key: str
    doc_type: str
    heading_path: list[str]
    content: str
    source_url: str
    licence: str
    section_no: str | None
    compilation_no: str | None
    instrument_id: str | None
    tenant_id: str | None
    score: float
    why: str            # dense | lexical | both | definition | crossref

    @property
    def label(self) -> str:
        return " > ".join(self.heading_path)


def _row_to_hit(row: sqlite3.Row, score: float, why: str) -> Hit:
    import json
    return Hit(
        citation_key=row["citation_key"],
        doc_type=row["doc_type"],
        heading_path=json.loads(row["heading_path"]),
        content=row["content"],
        source_url=row["source_url"],
        licence=row["licence"],
        section_no=row["section_no"],
        compilation_no=row["compilation_no"],
        instrument_id=row["instrument_id"],
        tenant_id=row["tenant_id"],
        score=score,
        why=why,
    )


def _rrf(ranked_lists: list[list[int]], k: int = config.RRF_K) -> dict[int, float]:
    """Reciprocal Rank Fusion.

    Uses rank position rather than raw scores, which sidesteps the fact that
    cosine similarity and BM25 are not on comparable scales.
    """
    fused: dict[int, float] = {}
    for lst in ranked_lists:
        for rank, vec_row in enumerate(lst, start=1):
            fused[vec_row] = fused.get(vec_row, 0.0) + 1.0 / (k + rank)
    return fused


# Cohere trial keys allow 10 rerank requests/minute. Without pacing, an eval
# run of 25 questions gets 429s partway through and silently falls back to
# RRF -- which would report "reranker: on" while half the queries were never
# reranked. Silent degradation in a measurement tool is worse than an error.
_rerank_calls: list[float] = []


def _rerank_wait() -> None:
    while True:
        now = time.time()
        _rerank_calls[:] = [t for t in _rerank_calls if now - t < 60.0]
        if len(_rerank_calls) < config.COHERE_RPM:
            _rerank_calls.append(now)
            return
        time.sleep(max(60.0 - (now - min(_rerank_calls)), 0.5) + 0.25)


def _rerank(query: str, hits: list[Hit], top_n: int,
            diag: dict) -> list[Hit]:
    """Cross-encoder rerank. Usually the biggest single quality lever.

    Embeddings are computed independently for query and document and never
    see each other; a cross-encoder scores them together.
    """
    if not config.COHERE_API_KEY or len(hits) <= top_n:
        diag["reranked"] = False
        return hits[:top_n]

    for attempt in range(3):
        try:
            import cohere
            _rerank_wait()
            co = cohere.ClientV2(config.COHERE_API_KEY)
            docs = [f"{h.label}\n{h.content[:2000]}" for h in hits]
            res = co.rerank(model=config.COHERE_RERANK_MODEL, query=query,
                            documents=docs, top_n=top_n)
            out = []
            for r in res.results:
                h = hits[r.index]
                h.score = float(r.relevance_score)
                out.append(h)
            diag["reranked"] = True
            return out
        except Exception as e:                       # noqa: BLE001
            msg = str(e).lower()
            if ("rate" in msg or "429" in msg or "too many" in msg) \
                    and attempt < 2:
                time.sleep(8 * (attempt + 1))
                continue
            # Record the failure rather than hiding it. An eval that says
            # "reranker on" while silently using RRF is a lie.
            diag["reranked"] = False
            diag["rerank_error"] = f"{type(e).__name__}: {e}"[:140]
            return hits[:top_n]
    diag["reranked"] = False
    return hits[:top_n]


def detect_terms(query: str) -> list[str]:
    q = query.lower()
    return [t for t in KEY_TERMS if t.lower() in q]


def retrieve(
    query: str,
    *,
    tenant_id: str | None = None,
    segment: str | None = None,
    as_of: str | None = None,
    final_k: int = config.FINAL_K,
) -> tuple[list[Hit], dict]:
    """Returns (hits, diagnostics)."""
    conn = store.connect()
    vectors = store.load_vectors()
    visible = store.visible_rows(conn, tenant_id, segment, as_of)
    diag: dict = {"visible_chunks": len(visible)}

    if not visible:
        conn.close()
        return [], diag

    # --- dense ------------------------------------------------------------
    # Degrade to lexical-only rather than failing the request if the
    # embedding provider is unreachable. A demo that falls back to BM25
    # beats a demo that 500s in front of a prospect.
    dense_ranked: list[int] = []
    dense_scores: dict[int, float] = {}
    try:
        qv = embedder.embed([query], query=True)[0]
        if vectors.shape[1] != qv.shape[0]:
            raise ValueError(
                f"embedding dimension mismatch: index has "
                f"{vectors.shape[1]}-dim vectors but EMBED_PROVIDER="
                f"{config.EMBED_PROVIDER} produces {qv.shape[0]}-dim. "
                f"The index was built with a different provider — rebuild "
                f"with: python -m index.build")
        rows = np.fromiter(visible.keys(), dtype=np.int64)
        sims = vectors[rows] @ qv
        order = np.argsort(-sims)[:config.DENSE_K]
        dense_ranked = [int(rows[i]) for i in order]
        dense_scores = {int(rows[i]): float(sims[i]) for i in order}
    except Exception as e:                           # noqa: BLE001
        diag["dense_error"] = f"{type(e).__name__}: {e}"[:160]
        diag["degraded"] = "lexical-only"

    # --- lexical ----------------------------------------------------------
    lex = store.lexical_search(conn, query, config.LEXICAL_K,
                               tenant_id, segment, as_of)
    lex_ranked = [vr for vr, _ in lex]

    fused = _rrf([dense_ranked, lex_ranked])
    diag["dense"] = len(dense_ranked)
    diag["lexical"] = len(lex_ranked)

    candidates: list[Hit] = []
    for vec_row, score in sorted(fused.items(), key=lambda x: -x[1]):
        row = visible.get(vec_row)
        if row is None:
            continue
        in_d, in_l = vec_row in dense_scores, vec_row in set(lex_ranked)
        why = "both" if in_d and in_l else ("dense" if in_d else "lexical")
        candidates.append(_row_to_hit(row, score, why))

    hits = _rerank(query, candidates, final_k, diag)

    seen = {h.citation_key for h in hits}

    # --- definition expansion --------------------------------------------
    terms = detect_terms(query)
    if terms:
        for row in store.find_definitions(conn, terms, tenant_id, segment, as_of):
            if row["citation_key"] not in seen:
                hits.append(_row_to_hit(row, 0.0, "definition"))
                seen.add(row["citation_key"])
    diag["definitions_added"] = len(terms)

    # --- explicit section lookup -----------------------------------------
    # If the user names a section, that section goes first regardless of what
    # similarity said. This is the case pure dense retrieval cannot serve at
    # all: 'section 36' and 'section 43' embed to nearly the same point.
    asked = [m.group(1) for m in _SECTION_Q.finditer(query)]
    if asked:
        pinned = []
        for row in store.sections_by_number(conn, asked, tenant_id,
                                            segment, as_of):
            hit = _row_to_hit(row, 1.0, "section-lookup")
            hits = [h for h in hits if h.citation_key != hit.citation_key]
            pinned.append(hit)
            seen.add(hit.citation_key)
        hits = pinned + hits
    diag["sections_requested"] = asked

    # --- one cross-reference hop ------------------------------------------
    # Legislation is a graph: 'subject to section 39', 'as defined in
    # section 5'. One hop only -- two pulls in half the Act.
    refs: list[str] = []
    for h in hits[:5]:
        refs += re.findall(r"\bsections?\s+(\d+[A-Z]{0,3})\b", h.content, re.I)
    refs = [r for r in dict.fromkeys(refs) if r not in asked][:4]
    if refs:
        for row in store.sections_by_number(conn, refs, tenant_id,
                                            segment, as_of):
            if row["citation_key"] not in seen:
                hits.append(_row_to_hit(row, 0.0, "crossref"))
                seen.add(row["citation_key"])
    diag["crossrefs_added"] = len(refs)

    diag["top_score"] = hits[0].score if hits else 0.0
    conn.close()
    return hits, diag
