"""Storage: SQLite (metadata + FTS5 lexical) + numpy (dense vectors).

Why not Postgres/pgvector, which the architecture doc specifies for
production? At ~2,000 chunks a brute-force numpy dot product is sub-
millisecond and needs no service to install, so the demo runs anywhere with
`python app/main.py`. The schema mirrors the Postgres design, so migrating
is a swap of this file.

Tenant isolation is a SQL predicate, never a prompt instruction. A prompt
is a request to a probabilistic system; a WHERE clause is a boundary.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np

import config
from ingest.chunker import Chunk

SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    rowid_pk        INTEGER PRIMARY KEY,
    citation_key    TEXT NOT NULL,
    doc_id          TEXT NOT NULL,
    doc_type        TEXT NOT NULL,
    instrument_id   TEXT,
    compilation_id  TEXT,
    compilation_no  TEXT,
    section_no      TEXT,
    heading_path    TEXT NOT NULL,
    content         TEXT NOT NULL,
    source_url      TEXT NOT NULL,
    licence         TEXT NOT NULL,
    effective_from  TEXT,
    effective_to    TEXT,
    segments        TEXT NOT NULL DEFAULT '[]',
    tenant_id       TEXT,
    defined_term    TEXT,
    cross_refs      TEXT NOT NULL DEFAULT '[]',
    content_hash    TEXT NOT NULL,
    vec_row         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tenant   ON chunks(tenant_id);
CREATE INDEX IF NOT EXISTS ix_doctype  ON chunks(doc_type);
CREATE INDEX IF NOT EXISTS ix_section  ON chunks(section_no);
CREATE INDEX IF NOT EXISTS ix_cite     ON chunks(citation_key);
CREATE INDEX IF NOT EXISTS ix_term     ON chunks(defined_term);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    content, heading_path, defined_term,
    content='chunks', content_rowid='rowid_pk', tokenize='porter unicode61'
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def reset() -> None:
    for p in (config.DB_PATH, config.VECTORS_PATH):
        if p.exists():
            p.unlink()


def write(chunks: list[Chunk], vectors: np.ndarray) -> None:
    if len(chunks) != vectors.shape[0]:
        raise ValueError(
            f"{len(chunks)} chunks but {vectors.shape[0]} vectors")

    conn = connect()
    with conn:
        conn.execute("DELETE FROM chunks")
        conn.execute("DELETE FROM chunks_fts")
        rows = []
        for i, c in enumerate(chunks):
            rows.append((
                i + 1, c.citation_key, c.doc_id, c.doc_type, c.instrument_id,
                c.compilation_id, c.compilation_no, c.section_no,
                json.dumps(c.heading_path), c.content, c.source_url, c.licence,
                c.effective_from, c.effective_to, json.dumps(c.segments),
                c.tenant_id, c.defined_term, json.dumps(c.cross_refs),
                c.content_hash, i,
            ))
        conn.executemany(
            "INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows)
        conn.execute(
            "INSERT INTO chunks_fts(rowid, content, heading_path, defined_term) "
            "SELECT rowid_pk, content, heading_path, "
            "COALESCE(defined_term,'') FROM chunks")
    conn.close()
    np.save(config.VECTORS_PATH, vectors)


def load_vectors() -> np.ndarray:
    if not config.VECTORS_PATH.exists():
        raise FileNotFoundError(
            "No vector index. Run:  python -m index.build")
    return np.load(config.VECTORS_PATH)


def _visible_sql(tenant_id: str | None, segment: str | None,
                 as_of: str | None) -> tuple[str, list]:
    """The isolation boundary. Everything else in the system trusts this."""
    sql = ["(tenant_id IS NULL"]
    args: list = []
    if tenant_id:
        sql.append(" OR tenant_id = ?")
        args.append(tenant_id)
    sql.append(")")

    if segment:
        sql.append(" AND (segments = '[]' OR segments LIKE ?)")
        args.append(f'%"{segment}"%')
    if as_of:
        sql.append(" AND (effective_from IS NULL OR effective_from <= ?)")
        args.append(as_of)
        sql.append(" AND (effective_to IS NULL OR effective_to > ?)")
        args.append(as_of)
    return "".join(sql), args


def visible_rows(conn, tenant_id, segment, as_of) -> dict[int, sqlite3.Row]:
    where, args = _visible_sql(tenant_id, segment, as_of)
    cur = conn.execute(f"SELECT * FROM chunks WHERE {where}", args)
    return {r["vec_row"]: r for r in cur.fetchall()}


def lexical_search(conn, query: str, k: int, tenant_id, segment, as_of
                   ) -> list[tuple[int, float]]:
    """BM25 via FTS5. Essential here: embeddings cannot match 'section 36'
    or distinguish 'suspicious matter' from 'suspicious transaction'."""
    where, args = _visible_sql(tenant_id, segment, as_of)
    safe = " OR ".join(
        f'"{t}"' for t in
        (w for w in query.replace('"', " ").split() if len(w) > 1)
    ) or '"aml"'
    sql = (f"SELECT c.vec_row AS vr, bm25(chunks_fts) AS score "
           f"FROM chunks_fts f JOIN chunks c ON c.rowid_pk = f.rowid "
           f"WHERE chunks_fts MATCH ? AND {where} "
           f"ORDER BY score LIMIT ?")
    try:
        cur = conn.execute(sql, [safe, *args, k])
    except sqlite3.OperationalError:
        return []
    return [(r["vr"], -float(r["score"])) for r in cur.fetchall()]


def by_citation(conn, keys: list[str]) -> dict[str, sqlite3.Row]:
    if not keys:
        return {}
    q = ",".join("?" * len(keys))
    cur = conn.execute(f"SELECT * FROM chunks WHERE citation_key IN ({q})", keys)
    return {r["citation_key"]: r for r in cur.fetchall()}


def find_definitions(conn, terms: list[str], tenant_id, segment, as_of
                     ) -> list[sqlite3.Row]:
    if not terms:
        return []
    where, args = _visible_sql(tenant_id, segment, as_of)
    # Prefix match, not equality: the Act defines 'beneficial owner of a
    # person other than an individual', so an exact match on 'beneficial
    # owner' would find nothing.
    clauses = " OR ".join(["LOWER(defined_term) LIKE ?"] * len(terms))
    cur = conn.execute(
        f"SELECT * FROM chunks WHERE doc_type='definition' "
        f"AND ({clauses}) AND {where} LIMIT 8",
        [*[f"{t.lower()}%" for t in terms], *args])
    return cur.fetchall()


def sections_by_number(conn, numbers: list[str], tenant_id, segment, as_of
                       ) -> list[sqlite3.Row]:
    """Cross-reference hop. Legislation is a graph: 'subject to section 39'."""
    if not numbers:
        return []
    where, args = _visible_sql(tenant_id, segment, as_of)
    q = ",".join("?" * len(numbers))
    cur = conn.execute(
        f"SELECT * FROM chunks WHERE section_no IN ({q}) "
        f"AND doc_type IN ('act','rules') AND {where} LIMIT 6",
        [*numbers, *args])
    return cur.fetchall()


def stats() -> dict:
    conn = connect()
    out = {"total": conn.execute("SELECT COUNT(*) c FROM chunks")
                         .fetchone()["c"]}
    for r in conn.execute(
            "SELECT doc_type, COUNT(*) c FROM chunks GROUP BY doc_type"):
        out[r["doc_type"]] = r["c"]
    out["tenants"] = [r["tenant_id"] for r in conn.execute(
        "SELECT DISTINCT tenant_id FROM chunks WHERE tenant_id IS NOT NULL")]
    conn.close()
    return out
