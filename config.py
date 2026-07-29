"""Central configuration for the AML/CTF knowledge assistant demo."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent
DATA = ROOT / "data"
CACHE = DATA / "cache"
TENANT_DIR = DATA / "tenants"
DB_PATH = DATA / "index.db"
VECTORS_PATH = DATA / "vectors.npy"
EVAL_RESULTS = DATA / "eval_results.json"
BUILD_MANIFEST = DATA / "build_manifest.json"

for _d in (DATA, CACHE, TENANT_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- providers

# Generation: "groq" (free) | "anthropic" | "gemini" (free) | "ollama" (local)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq").lower()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")

# Embeddings: "local" (free, no key) | "voyage" | "gemini" | "openai"
EMBED_PROVIDER = os.getenv("EMBED_PROVIDER", "local").lower()
LOCAL_EMBED_MODEL = os.getenv("LOCAL_EMBED_MODEL", "BAAI/bge-small-en-v1.5")
# Keep this small. fastembed's default (256) OOM-kills on long legal chunks.
LOCAL_EMBED_BATCH = int(os.getenv("LOCAL_EMBED_BATCH", "8"))
VOYAGE_API_KEY = os.getenv("VOYAGE_API_KEY", "")
VOYAGE_MODEL = os.getenv("VOYAGE_MODEL", "voyage-4-lite")
# Voyage free tier WITHOUT a payment method on file: 3 requests/min and
# 10,000 tokens/min. (The 200M free tokens are a separate, generous quota --
# the throttle is what bites, not the allowance.) Adding a card raises these
# to 2,000 RPM / 8M TPM and the free tokens still apply.
VOYAGE_RPM = int(os.getenv("VOYAGE_RPM", "3"))
VOYAGE_TPM = int(os.getenv("VOYAGE_TPM", "10000"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")

# Optional reranker. Without it the pipeline falls back to RRF order.
COHERE_API_KEY = os.getenv("COHERE_API_KEY", "")
COHERE_RERANK_MODEL = os.getenv("COHERE_RERANK_MODEL", "rerank-english-v3.0")
# Trial keys: 10 rerank req/min, and 1,000 API calls/month across everything.
COHERE_RPM = int(os.getenv("COHERE_RPM", "10"))

# ---------------------------------------------------------------- retrieval

DENSE_K = 30           # candidates from vector search
LEXICAL_K = 30         # candidates from FTS5/BM25
RRF_K = 60             # Reciprocal Rank Fusion constant
FINAL_K = 8            # chunks passed to the model
# Refuse when the top reranked score falls below this.
# Default 0 = disabled. Rerank scores are NOT comparable across models -- a
# genuinely relevant passage scores ~0.04 on rerank-english-v3.0 and ~0.9 on
# others. A guessed threshold refuses everything, which kills the product
# faster than a wrong answer would. Calibrate it:
#     python scripts/calibrate_threshold.py
MIN_RERANK_SCORE = float(os.getenv("MIN_RERANK_SCORE", "0"))

CHUNK_TARGET_TOKENS = 500
CHUNK_MAX_TOKENS = 900

# ---------------------------------------------------------------- corpus

# Instruments to index. Dates come from the FRL API at ingest time.
INSTRUMENTS = [
    {"title_id": "C2006A00169", "short": "AML/CTF Act 2006", "doc_type": "act"},
    {"title_id": "F2025L01026", "short": "AML/CTF Rules 2025", "doc_type": "rules"},
    {"title_id": "F2007L01000",
     "short": "AML/CTF (Class Exemptions and Other Matters) Rules 2007",
     "doc_type": "rules"},
    {"title_id": "F2026L00393",
     "short": "AML/CTF Transitional Rules 2026", "doc_type": "rules"},
]

FRL_API = "https://api.prod.legislation.gov.au/v1"
FRL_WEB = "https://www.legislation.gov.au"
FRL_CRAWL_DELAY = 10  # robots.txt: Crawl-delay: 10 — honoured in frl.py

FRL_ATTRIBUTION = (
    "Sourced from the Federal Register of Legislation. For the latest information "
    "on Australian Government law please go to https://www.legislation.gov.au"
)
AUSTRAC_ATTRIBUTION = "© AUSTRAC for the Commonwealth of Australia 2019"

# AUSTRAC guidance pages for the accountant segment demo.
# Their site is intermittently unreachable — the fetcher retries and records
# failures rather than silently skipping. See ingest/austrac.py.
AUSTRAC_BASE = "https://www.austrac.gov.au"
# Fail fast. AUSTRAC blocks many hosts outright; long retries just make the
# build look hung. Worst case with these values is well under a minute for
# the whole page list, vs 70+ minutes at 5 attempts x 90s.
AUSTRAC_TIMEOUT = int(os.getenv("AUSTRAC_TIMEOUT", "15"))
AUSTRAC_ATTEMPTS = int(os.getenv("AUSTRAC_ATTEMPTS", "2"))
AUSTRAC_CIRCUIT_BREAK = int(os.getenv("AUSTRAC_CIRCUIT_BREAK", "3"))
AUSTRAC_PAGES = [
    "/industry-and-business/obligations-and-guidance/your-obligations",
    "/industry-and-business/obligations-and-guidance/your-obligations/governance",
    "/industry-and-business/obligations-and-guidance/your-obligations/amlctf-program",
    "/industry-and-business/obligations-and-guidance/your-obligations/customer-due-diligence",
    "/industry-and-business/obligations-and-guidance/your-obligations/reporting",
    "/industry-and-business/obligations-and-guidance/your-obligations/record-keeping",
    "/industry-and-business/obligations-and-guidance/your-obligations/"
    "personnel-due-diligence-and-training",
    "/industry-and-business/obligations-and-guidance/your-obligations/reporting-groups",
    "/reforms/sector-specific-guidance/accountant-guidance",
]
AUSTRAC_PDFS = [
    "/sites/default/files/2025-12/AML%20CTF%20program%20quick%20guide.pdf",
]

# Canonicalisation: AUSTRAC serves the same content under several namespaces.
AUSTRAC_NAMESPACE_ALIASES = [
    ("/about-us/amlctf-reform/reforms-guidance/", "/amlctf-reform/reforms-guidance/"),
    ("/business/core-guidance/",
     "/industry-and-business/obligations-and-guidance/"),
    ("/business/how-comply-guidance-and-resources/",
     "/industry-and-business/obligations-and-guidance/"),
    ("/industry-and-business/obligations-and-guidance/program-starter-kits/",
     "/reforms/sector-specific-guidance/"),
]

SEGMENTS = ["accountant", "lawyer", "real_estate", "jeweller", "tcsp"]
DEMO_SEGMENT = os.getenv("DEMO_SEGMENT", "accountant")
