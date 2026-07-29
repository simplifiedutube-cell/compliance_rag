"""Demo web app.

Two surfaces, deliberately separated:

  /             chat -- what a client sees. Public.
  /evaluation   golden-set results, refusal rates, per-case outcomes.
  /corpus       every indexed citation key and the storage layout.

The latter two are internal. They disclose the eval set and the full shape of
the index, so they sit on their own routes behind HTTP Basic and are served
out of app/private/, which is NOT mounted as static files -- mounting it would
let anyone fetch the HTML directly and skip the auth dependency.

    uvicorn app.main:app --reload --port 8000
"""
from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import Depends, FastAPI, HTTPException, status        # noqa: E402
from fastapi.responses import FileResponse, JSONResponse           # noqa: E402
from fastapi.security import HTTPBasic, HTTPBasicCredentials       # noqa: E402
from fastapi.staticfiles import StaticFiles                        # noqa: E402
from pydantic import BaseModel                                     # noqa: E402

import config                                                      # noqa: E402
from index import embed as embedder                                # noqa: E402
from index import store                                            # noqa: E402
from ingest import tenant                                          # noqa: E402
from query import llm                                              # noqa: E402
from query.answer import ask, escalation_packet                    # noqa: E402

app = FastAPI(title="AML/CTF Knowledge Assistant — Demo")
STATIC = Path(__file__).parent / "static"
PRIVATE = Path(__file__).parent / "private"

app.mount("/static", StaticFiles(directory=STATIC), name="static")

_basic = HTTPBasic(auto_error=False)


def require_admin(
    creds: HTTPBasicCredentials | None = Depends(_basic),
) -> str:
    """Gate the internal routes.

    With no ADMIN_TOKEN set we 404 rather than 401. On a public host an
    unconfigured deployment should look like it has no dashboard at all,
    not like it has one you have yet to guess the password for.
    """
    if not config.ADMIN_TOKEN:
        raise HTTPException(status_code=404)
    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Basic"},
        )
    ok_user = secrets.compare_digest(creds.username, config.ADMIN_USER)
    ok_pass = secrets.compare_digest(creds.password, config.ADMIN_TOKEN)
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Basic"},
        )
    return creds.username

TENANT_LABELS = {
    "meridian": "Meridian Accounting Partners",
    "corvid": "Corvid Advisory",
}


class Query(BaseModel):
    question: str
    tenant_id: str | None = None
    segment: str = config.DEMO_SEGMENT
    as_of: str | None = None


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


@app.get("/")
def index() -> FileResponse:
    """Chat only. No link to the internal routes from here."""
    return FileResponse(STATIC / "index.html")


@app.get("/evaluation")
def page_evaluation(_: str = Depends(require_admin)) -> FileResponse:
    return FileResponse(PRIVATE / "evaluation.html")


@app.get("/corpus")
def page_corpus(_: str = Depends(require_admin)) -> FileResponse:
    return FileResponse(PRIVATE / "corpus.html")


@app.get("/api/config")
def get_config() -> dict:
    try:
        st = store.stats()
    except Exception:                                # noqa: BLE001
        st = {"total": 0}
    return {
        "tenants": [{"id": t, "label": TENANT_LABELS.get(t, t.title())}
                    for t in tenant.list_tenants()],
        "segments": config.SEGMENTS,
        "default_segment": config.DEMO_SEGMENT,
        "stats": st,
        "attribution": [config.FRL_ATTRIBUTION, config.AUSTRAC_ATTRIBUTION],
        "providers": {
            "embedding": embedder.provider_name(),
            "generation": llm.provider_label(),
            "generation_available": llm.available(),
            "trains_on_your_data": llm.trains_on_your_data(),
            "reranker": (config.COHERE_RERANK_MODEL
                         if config.COHERE_API_KEY else None),
        },
    }


@app.get("/api/metrics")
def api_metrics(_: str = Depends(require_admin)) -> JSONResponse:
    """Everything the dashboard renders: build manifest + eval results."""
    manifest = _read_json(config.BUILD_MANIFEST)
    evals = _read_json(config.EVAL_RESULTS)

    where = {
        "note": ("Source documents are not stored in the repo. They are "
                 "downloaded at build time and indexed into the two files "
                 "below."),
        "downloaded_cache": str(config.CACHE),
        "sqlite_index": str(config.DB_PATH),
        "vectors": str(config.VECTORS_PATH),
        "sqlite_bytes": (config.DB_PATH.stat().st_size
                         if config.DB_PATH.exists() else 0),
        "vectors_bytes": (config.VECTORS_PATH.stat().st_size
                          if config.VECTORS_PATH.exists() else 0),
        "cache_files": (len(list(config.CACHE.glob("*")))
                        if config.CACHE.exists() else 0),
    }
    return JSONResponse({"manifest": manifest, "eval": evals,
                         "storage": where})


@app.get("/api/corpus")
def api_corpus(limit: int = 400, q: str = "", doc_type: str = "",
               tenant_id: str = "",
               _: str = Depends(require_admin)) -> JSONResponse:
    """Browse what is actually indexed. Internal: lists every citation key."""
    conn = store.connect()
    sql = ["SELECT citation_key, doc_type, heading_path, section_no, "
           "tenant_id, licence, compilation_no, effective_from, "
           "LENGTH(content) AS chars FROM chunks WHERE 1=1"]
    args: list = []
    if doc_type:
        sql.append(" AND doc_type = ?")
        args.append(doc_type)
    if tenant_id:
        sql.append(" AND tenant_id = ?")
        args.append(tenant_id)
    if q:
        sql.append(" AND (heading_path LIKE ? OR citation_key LIKE ?)")
        args += [f"%{q}%", f"%{q}%"]
    sql.append(" ORDER BY doc_type, citation_key LIMIT ?")
    args.append(min(limit, 2000))

    rows = conn.execute("".join(sql), args).fetchall()
    total = conn.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"]
    conn.close()
    return JSONResponse({
        "total": total,
        "returned": len(rows),
        "rows": [{
            "citation_key": r["citation_key"],
            "doc_type": r["doc_type"],
            "heading_path": json.loads(r["heading_path"]),
            "section_no": r["section_no"],
            "tenant_id": r["tenant_id"],
            "licence": r["licence"],
            "compilation_no": r["compilation_no"],
            "effective_from": r["effective_from"],
            "chars": r["chars"],
        } for r in rows],
    })


@app.post("/api/ask")
def api_ask(q: Query) -> JSONResponse:
    if not q.question.strip():
        return JSONResponse({"error": "empty question"}, status_code=400)

    a = ask(q.question, tenant_id=q.tenant_id, segment=q.segment,
            as_of=q.as_of)

    return JSONResponse({
        "answer_type": a.answer_type,
        "summary": a.summary,
        "claims": [{"text": c.text, "citations": c.citations}
                   for c in a.claims],
        "caveats": a.caveats,
        "escalate": a.escalate,
        "escalation_reason": a.escalation_reason,
        "corpus_version": a.corpus_version,
        "diagnostics": a.diagnostics,
        "validation": a.validation,
        "sources": [{
            "citation_key": h.citation_key,
            "label": h.label,
            "doc_type": h.doc_type,
            "tenant_id": h.tenant_id,
            "licence": h.licence,
            "source_url": h.source_url,
            "why": h.why,
            "score": round(h.score, 4),
            "content": h.content[:4000],
        } for h in a.hits],
        "escalation_packet": (escalation_packet(q.question, a, q.tenant_id)
                              if a.escalate else None),
    })


@app.get("/api/source/{citation_key:path}")
def api_source(citation_key: str, tenant_id: str = "") -> JSONResponse:
    """Read one chunk, scoped to the caller's client.

    Retrieval is tenant-filtered, but this route is reachable with a bare
    citation key. Without the owner check below, a Corvid session could read
    Meridian's SOPs by URL -- which is precisely the leak the whole product
    claims not to have. Shared law is visible to everyone; a tenant-owned
    chunk 404s for anyone else, so the key does not confirm the document
    even exists.
    """
    conn = store.connect()
    rows = store.by_citation(conn, [citation_key])
    conn.close()
    r = rows.get(citation_key)
    if not r:
        return JSONResponse({"error": "not found"}, status_code=404)

    owner = r["tenant_id"]
    if owner and owner != tenant_id:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({
        "citation_key": r["citation_key"],
        "heading_path": json.loads(r["heading_path"]),
        "content": r["content"],
        "source_url": r["source_url"],
        "licence": r["licence"],
        "compilation_no": r["compilation_no"],
        "effective_from": r["effective_from"],
    })
