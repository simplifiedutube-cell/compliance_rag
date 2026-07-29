"""Build the index.

    python -m index.build              # everything
    python -m index.build --no-austrac # legislation + tenants only
    python -m index.build --no-context # skip contextual headers (faster/cheaper)

Never blocks on AUSTRAC: if the site is unreachable it records the failures
and carries on with legislation, which is the licence-clean core anyway.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import config
from index import embed as embedder
from index import store
from ingest import austrac, frl, tenant
from ingest.chunker import (Chunk, chunk_definitions, chunk_legislation,
                            chunk_prose)
from ingest.epub_parser import parse

MANUAL_AUSTRAC = config.DATA / "austrac_manual"


def log(msg: str) -> None:
    print(f"  {msg}", flush=True)


# ------------------------------------------------------------- legislation

MANIFEST: dict = {"instruments": [], "austrac": {}, "tenants": []}
_EMBED_DIM: dict = {}


def build_legislation() -> list[Chunk]:
    chunks: list[Chunk] = []
    for spec in config.INSTRUMENTS:
        tid = spec["title_id"]
        print(f"\n[legislation] {tid} — {spec['short']}")
        try:
            version, doc = frl.fetch_instrument(tid)
        except Exception as e:                       # noqa: BLE001
            log(f"FAILED: {type(e).__name__}: {e}")
            continue

        sections, definitions = parse(doc)
        log(f"compilation {version.compilation_number or 'as-made'} "
            f"({version.register_id}), in force {version.start}")
        log(f"{len(sections)} sections, {len(definitions)} definitions")

        common = dict(
            doc_id=tid,
            instrument_id=tid,
            compilation_id=version.register_id,
            compilation_no=version.compilation_number,
            citation_prefix=version.citation_prefix,
            source_url=version.source_url,
            effective_from=version.start,
            effective_to=version.end,
        )
        sec_chunks = chunk_legislation(sections, doc_type=spec["doc_type"],
                                       **common)
        def_chunks = chunk_definitions(definitions, **common)
        log(f"-> {len(sec_chunks)} section chunks, {len(def_chunks)} definitions")
        chunks += sec_chunks + def_chunks

        MANIFEST["instruments"].append({
            "title_id": tid,
            "name": version.name,
            "short": spec["short"],
            "compilation_id": version.register_id,
            "compilation_no": version.compilation_number,
            "in_force_from": version.start,
            "sections": len(sections),
            "definitions": len(definitions),
            "chunks": len(sec_chunks) + len(def_chunks),
            "words": sum(len(c.content.split()) for c in sec_chunks + def_chunks),
            "source_url": version.source_url,
            "licence": "CC-BY-4.0",
        })
    return chunks


# ----------------------------------------------------------------- austrac

def build_austrac() -> list[Chunk]:
    print("\n[austrac] guidance")
    chunks: list[Chunk] = []

    docs, failures = austrac.ingest_pages(config.AUSTRAC_PAGES)
    for d in docs:
        chunks += chunk_prose(
            d["blocks"],
            doc_id=d["url"],
            doc_type="guidance",
            source_url=d["url"],
            licence="AUSTRAC-CC",
            segments=[],
            citation_prefix="austrac:" + d["url"].rsplit("/", 1)[-1][:40],
        )
    skipped = sum(1 for f in failures if "skipped" in f.error)
    log(f"{len(docs)} pages ingested, {len(failures)} failed"
        + (f" ({skipped} skipped by circuit breaker — AUSTRAC is blocking "
           f"this host)" if skipped else ""))
    MANIFEST["austrac"] = {
        "pages_ok": len(docs), "pages_failed": len(failures),
        "failed_urls": [f.url for f in failures][:20],
        "licence": "AUSTRAC-CC",
    }

    # Manual fallback: pages saved from a browser into data/austrac_manual/.
    # AUSTRAC's site is unreachable from many hosts; this keeps the build
    # working without pretending the content was fetched.
    if MANUAL_AUSTRAC.exists():
        manual = sorted(MANUAL_AUSTRAC.glob("*.html"))
        for f in manual:
            blocks = austrac.extract_blocks(f.read_bytes(), f.stem)
            chunks += chunk_prose(
                blocks,
                doc_id=f"austrac-manual/{f.stem}",
                doc_type="guidance",
                source_url=f"{config.AUSTRAC_BASE}/  [saved: {f.name}]",
                licence="AUSTRAC-CC",
                citation_prefix=f"austrac:{f.stem[:40]}",
            )
        if manual:
            log(f"{len(manual)} manually-saved pages ingested")

    if failures and not chunks:
        log("AUSTRAC unreachable. The demo still works on legislation alone.")
        log(f"To add guidance: save pages as HTML into {MANUAL_AUSTRAC}")
    return chunks


# ----------------------------------------------------------------- tenants

def build_tenants() -> list[Chunk]:
    print("\n[tenants]")
    chunks: list[Chunk] = []
    for tid in tenant.list_tenants():
        docs = tenant.load_tenant(tid)
        n = 0
        for d in docs:
            c = chunk_prose(
                d["blocks"],
                doc_id=d["doc_id"],
                doc_type=d["doc_type"],
                source_url=f"tenant://{d['doc_id']}",
                licence="tenant-private",
                tenant_id=tid,
                citation_prefix=f"{d['doc_id']}",
            )
            chunks += c
            n += len(c)
        log(f"{tid}: {len(docs)} documents -> {n} chunks")
        MANIFEST["tenants"].append({
            "tenant_id": tid, "documents": len(docs), "chunks": n,
            "titles": [d["title"] for d in docs],
        })
    return chunks


# ------------------------------------------------------- contextual headers

CONTEXT_PROMPT = """You are indexing Australian AML/CTF compliance documents.

Write ONE sentence (max 30 words) describing what this passage covers, so it
can be found by semantic search. Name the obligation or concept explicitly.
Do not start with "This passage" or "This section". Output the sentence only.

Document location: {path}

Passage:
{body}"""


def add_context_headers(chunks: list[Chunk], limit: int | None = None) -> None:
    """Prepend a generated one-line summary before embedding.

    Statutory text is full of "the entity must..." with the subject three
    levels up. Without context, such a chunk embeds to roughly "something
    must happen" and is never retrieved correctly.
    """
    from query import llm
    if not llm.available():
        log(f"no generation provider ({config.LLM_PROVIDER}) — "
            f"skipping contextual headers")
        return

    todo = [c for c in chunks if len(c.content) > 200][:limit or len(chunks)]
    print(f"\n[context] generating headers for {len(todo)} chunks")

    for i, c in enumerate(todo, 1):
        try:
            c.context_header = llm.generate_text(
                "You write one-line search summaries. Output the sentence only.",
                CONTEXT_PROMPT.format(path=" > ".join(c.heading_path),
                                      body=c.content[:2500]),
                max_tokens=80)
        except Exception as e:                       # noqa: BLE001
            log(f"context failed for {c.citation_key}: {type(e).__name__}")
        if i % 100 == 0:
            log(f"{i}/{len(todo)}")


# -------------------------------------------------------------------- main

def write_manifest(chunks: list[Chunk], embed_provider: str,
                   contextual: bool) -> None:
    by_type: dict[str, int] = {}
    for c in chunks:
        by_type[c.doc_type] = by_type.get(c.doc_type, 0) + 1
    words = sum(len(c.content.split()) for c in chunks)
    MANIFEST.update({
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_chunks": len(chunks),
        "by_doc_type": by_type,
        "words": words,
        "est_tokens": int(words * 1.4),
        "embed_provider": embed_provider,
        "embed_dim": _EMBED_DIM.get("dim"),
        "contextual_headers": contextual,
        "db_path": str(config.DB_PATH),
        "vectors_path": str(config.VECTORS_PATH),
    })
    config.BUILD_MANIFEST.write_text(json.dumps(MANIFEST, indent=2))
    print(f"Manifest written to {config.BUILD_MANIFEST}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-austrac", action="store_true")
    ap.add_argument("--no-tenants", action="store_true")
    ap.add_argument("--no-context", action="store_true")
    ap.add_argument("--no-embed", action="store_true",
                    help="parse and chunk only; skip embedding (no API key)")
    ap.add_argument("--fake-embed", action="store_true",
                    help="index with random vectors — smoke-tests the store, "
                         "BM25 and tenant isolation without any API key. "
                         "Dense retrieval will be meaningless.")
    args = ap.parse_args()

    store.reset()
    chunks = build_legislation()
    if not args.no_austrac:
        chunks += build_austrac()
    if not args.no_tenants:
        chunks += build_tenants()

    if not chunks:
        print("\nNothing to index.", file=sys.stderr)
        return 1

    print(f"\n[total] {len(chunks)} chunks")
    by_type: dict[str, int] = {}
    for c in chunks:
        by_type[c.doc_type] = by_type.get(c.doc_type, 0) + 1
    for k, v in sorted(by_type.items()):
        log(f"{k:12s} {v}")

    words = sum(len(c.content.split()) for c in chunks)
    log(f"~{words:,} words  ~{int(words * 1.4):,} tokens")

    if args.no_embed:
        print("\n--no-embed: stopping before embedding.")
        return 0

    if args.fake_embed:
        print("\n[embed] FAKE vectors — smoke test only, dense retrieval "
              "is meaningless")
        rng = np.random.default_rng(7)
        v = rng.normal(size=(len(chunks), 256)).astype(np.float32)
        vectors = v / np.linalg.norm(v, axis=1, keepdims=True)
        store.write(chunks, vectors)
        write_manifest(chunks, "fake (smoke test)", False)
        print(f"\nIndex written to {config.DB_PATH}")
        return 0

    if not args.no_context:
        add_context_headers(chunks)

    print("\n[embed] embedding chunks...")
    vectors = embedder.embed([c.embed_text for c in chunks])
    _EMBED_DIM["dim"] = int(vectors.shape[1])
    log(f"{vectors.shape[0]} vectors, dim {vectors.shape[1]}")

    store.write(chunks, vectors)
    write_manifest(chunks, embedder.provider_name(), not args.no_context)
    print(f"\nIndex written to {config.DB_PATH}")
    print(f"Vectors written to {config.VECTORS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
