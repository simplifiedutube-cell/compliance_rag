# AML/CTF Knowledge Assistant — Demo

A working RAG assistant for Australian Tranche 2 AML/CTF compliance, built to
open sales conversations. Not the product — see `04-delivery-plan.md` in
`../build/` for what a real engagement looks like.

**Read `demo-spec.md` before demoing.** It has the four demo moments, the
25-question script, and a blunt section on the 16 competitors who already
ship an AI assistant.

---

## Quick start

```bash
cd demo
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # add ONE key: GROQ_API_KEY (free, no card)
python scripts/preflight.py   # verify every key works before you build
python -m index.build         # downloads legislation, chunks, embeds locally
python -m eval.run_eval       # measure it
uvicorn app.main:app --port 8000
```

**New here, or just added API keys? Read `GETTING-STARTED.md`** — step-by-step
with the exact `.env` for a Groq + Voyage + Cohere setup, what each metric
should read, and a troubleshooting table.

Open http://127.0.0.1:8000 — three tabs: **Chat**, **Evaluation**, **Corpus**.

### API keys: exactly one, and it's free

| Need | Default | Key? | Cost |
| --- | --- | --- | --- |
| Embeddings | **local** — `BAAI/bge-small-en-v1.5` via fastembed | **none** | $0 |
| Generation | **Groq** — `llama-3.3-70b-versatile` | 1 free key, no card | $0 |
| Reranker | off by default | optional | — |

**Get the Groq key:** https://console.groq.com/keys — GitHub or Google login,
no credit card. Free tier is 30 requests/minute and 1,000/day, which covers a
demo several times over.

Groq matters for more than the price. Its Services Agreement §4.2 says Groq
"is not permitted to use Inputs or Outputs for training or fine-tuning any AI
Model Services or other models." It is the **only** free tier here with that
clause — and you are selling to compliance companies who will ask.

Embeddings never leave the machine at all, which is a stronger answer still.

### Alternatives, ranked

| Provider | Free tier | The catch |
| --- | --- | --- |
| **Groq** (gen) | 30 rpm / 1,000 rpd, no card | Rate limit is the ceiling, not cost |
| **local fastembed** (embed) | unlimited, no key | ~2–8 min one-off build; nothing leaves the machine |
| **Voyage** (embed) | 200M free tokens on signup | Best hosted quality. Data-use position not published — get it in writing before pointing it at client documents |
| **Gemini** (both) | free of charge | ⚠️ Google's terms say free-tier input **and output** are used to improve their products and human reviewers may read them. Their docs say "Do not submit sensitive, confidential, or personal information to the Unpaid Services." Synthetic demo data only. $5 of billing moves you to the paid tier where this doesn't apply. |
| **Ollama** (gen) | unlimited, local | Slower; weaker on multi-hop |
| **Anthropic / OpenAI** | none | ~$5 minimum credit |

If you skipped free tiers entirely, the whole project costs about **$0.30–$2.30**
in tokens. The real cost is the $5 minimum purchase, not usage.

### Try it before signing up for anything

```bash
python -m index.build --no-austrac --fake-embed   # random vectors, instant
python -m eval.run_eval --retrieval               # BM25 + isolation only
uvicorn app.main:app --port 8000
```

BM25, tenant isolation, section lookup, definitions, every refusal rule, and
the whole dashboard work with **no key and no model download**. If the
embedding provider is unreachable mid-demo, retrieval degrades to
lexical-only rather than erroring.

---

## Where the indexed documents actually live

Nothing is committed to the repo. Source documents are downloaded at build
time and turned into two files:

```
data/cache/          downloaded ePub XHTML + AUSTRAC HTML  (re-fetchable, disposable)
data/index.db        SQLite — chunk text, metadata, BM25 index   (~1.8 MB)
data/vectors.npy     numpy float32 array, one row per chunk       (~1.4 MB)
data/build_manifest.json   what was indexed, which compilation, when
data/eval_results.json     latest eval run — the dashboard reads this
```

The **Corpus tab** shows all of this live: exact paths, file sizes, every
instrument with its compilation number, and a searchable browser over all 944
chunks. Click any row to read the indexed text.

Rebuild any time with `python -m index.build`. Delete `data/` and nothing is
lost.

## What's indexed

**944 chunks, ~128,000 words, ~179,000 tokens.** The whole vector index is a
few megabytes and costs well under a dollar to embed.

| Source | Chunks | Licence |
| --- | --- | --- |
| AML/CTF Act 2006, compilation 62 (`C2026C00274`) | 371 + 259 definitions | CC BY 4.0 |
| AML/CTF Rules 2025, compilation 1 (`F2026C00274`) | 172 + 36 definitions | CC BY 4.0 |
| Class Exemptions Rules 2007, compilation 77 (`F2026C00269`) | 6 | CC BY 4.0 |
| Transitional Rules 2026, compilation 1 (`F2026C00452`) | 21 + 8 definitions | CC BY 4.0 |
| AUSTRAC guidance | varies | AUSTRAC CC |
| Meridian Accounting Partners (synthetic tenant) | 56 | written for this demo |
| Corvid Advisory (synthetic tenant) | 15 | written for this demo |

Legislation is fetched from the Federal Register of Legislation's public
OData API — four downloads, `robots.txt` honoured with its `Crawl-delay: 10`.
CC BY 4.0 permits commercial use explicitly. Attribution renders in the UI.

Nothing here is scraped from a source that prohibits it. Not indexed, and
deliberately: AustLII (blocks automated access, claims copyright in its
markup), CPA Australia (bans scraping and commercial exploitation), CA ANZ
(terms cl 19.2 prohibits submitting their content to generative AI), Law
Society guides, law firm alerts, competitor content.

### If AUSTRAC won't fetch

`austrac.gov.au` refuses automated requests from many hosts — roughly 1 in 4
succeed from cloud egress, 0 in 8 from some networks. The build never blocks
on it: failures are logged and legislation carries the demo.

To add guidance, save the pages from your browser into
`data/austrac_manual/` (Cmd+S → "Web Page, HTML Only"). `index/build.py`
picks up every `*.html` there. See that folder's README for which pages to
grab first.

---

## The four demo moments

Everything else is decoration. In this order:

**1. The citation.** *"When must a threshold transaction report be given to
AUSTRAC?"* — answer carries the section number, click it and the actual text
of the Act opens with its compilation number and in-force date.

**2. The refusal.** *"Am I a reporting entity?"* — it refuses, surfaces the
criteria, and hands over an escalation. This is the question your prospect's
clients ask most and the one they can least afford to get wrong. Most AI
demos show capability; showing a deliberate, well-designed refusal is what
separates you from someone who wired up a chatbot over a weekend.

**3. Their own documents.** *"What does our SOP say about onboarding a trust
client?"* — cites SOP 14, not the Act. No competitor's assistant can do this.
It is the entire differentiation.

**4. Isolation.** Switch the client dropdown to Corvid Advisory, ask the same
question. Meridian's SOP is invisible. Enforced in SQL, not in the prompt.

---

## Architecture

```
FRL OData API ──► ePub ──► structure-aware chunker ──┐
AUSTRAC HTML ────► readability ──► heading blocks ───┼──► embed ──► SQLite
tenant markdown ─► heading blocks ───────────────────┘             + numpy

query ──► hard refusal rules
      ──► SQL filter (tenant · segment · effective date)
      ──► hybrid: dense + BM25 ──► RRF ──► rerank
      ──► pin explicitly-named sections
      ──► expand definitions, one cross-reference hop
      ──► generate structured JSON
      ──► VALIDATE every citation against what was retrieved
      ──► render, or refuse and escalate
```

### Why the ePub, not the PDF

The ePub's XHTML carries the Office of Parliamentary Counsel's own drafting
classes — `ActHead5`, `CharSectno`, `subsection`, `Definition`, `Penalty`.
That gives section-accurate chunking and reliable section numbers from the
authoritative source. Parsing the PDF would mean regex-guessing both. There
is no XML format.

Verified against compilation 62: 332 sections, 260 definitions, 1,390 endnote
rows (dropped — roughly 25% of the Act's tokens and pure retrieval noise).

### Citation keys are minted, not borrowed

FRL section anchors are Word bookmarks regenerated on every compilation. This
was tested directly: across the 319 sections common to compilations 60 and
62, **zero anchor IDs survived**. So keys are minted from `CharSectno`:

```
C2006A00169@62#s5A          Act, compilation 62, section 5A
F2025L01026@1#s9.2          Rules 2025, section 9, second chunk
meridian/sop-14-...#b9      tenant document, ninth block
```

### Why hybrid retrieval is mandatory here

Dense retrieval fails on this corpus in two specific ways: terms of art
("suspicious matter" vs "suspicious transaction" sit almost on top of each
other in embedding space but are different obligations with different
deadlines), and identifiers ("section 36" and "section 43" embed to nearly
the same point). BM25 handles both. Ask *"what does section 5A say?"* with
dense-only and watch it fail — that failure teaches more than any blog post.

### Cite-or-refuse is mechanical, not prompted

The model returns JSON with a required `citations` field. Before anything
renders, every citation is checked against the chunks actually retrieved for
that query. A claim with no valid citation is dropped; if nothing survives,
the answer becomes a refusal.

This catches *fabricated* citations. It does **not** catch a real citation
attached to a claim it doesn't support — that needs human sampling. Two
different metrics, and only one can be automated. See
`../build/03-evaluation.md`.

### Hard refusal rules

Run before the model, not left to its judgement:

| Trigger | Behaviour |
| --- | --- |
| "Am I a reporting entity?" | Never answered. Criteria + sections + escalate. |
| "Is this suspicious?" / "Should I report?" | Judgement call — refuse. |
| Other jurisdictions | Out of corpus — refuse. |
| Deadline question with no grounded claim | Fails closed. Never estimates. |
| Retrieval below confidence threshold | Refuse and escalate. |

Over-refusal is tracked as a failure too. An assistant that refuses 40% of
questions gets switched off in a month. Target ≤ 20%.

---

## Dashboard

Three tabs at http://127.0.0.1:8000

**Chat** — the demo surface. Answer with clickable citations, retrieved-source
panel showing why each chunk was pulled (dense / lexical / both / definition /
cross-ref), and the pre-researched escalation packet on any refusal.

**Evaluation** — the eval harness rendered. Headline tiles for cross-tenant
leaks, refusal recall, invalid citations, over-refusals, refusal rate and
latency; a breakdown of where retrieved sources came from (the lexical-only
count is the concrete argument for hybrid retrieval); pass/fail by category;
and every case in a table. Reads `data/eval_results.json`, so re-run the
harness and refresh.

**Corpus** — where the documents live on disk, every instrument with its
compilation ID and licence, chunk composition, AUSTRAC fetch status, and a
searchable browser over the whole index.

Colours are from a CVD-validated palette; light and dark are separately
stepped, and every chart carries direct labels so colour never carries meaning
alone.

## Evaluation

```bash
python -m eval.run_eval --retrieval   # no API key needed
python -m eval.run_eval               # full pipeline
```

25 cases in `eval/golden.yaml`: 10 legislation, 5 tenant, 6 refusals, 4 edge
cases. Reports refusal recall (target 1.00), over-refusals, refusal rate,
invalid citations (target 0), and cross-tenant leaks — **any leak exits
non-zero**, because it's a P0.

Current state, lexical-only, no embeddings: **25/25 run clean, 0 leaks.**

This is a demo set, not a production golden set. The real one comes from the
client's own support inbox — ask for six months of it in the first meeting.

---

## Storage: why SQLite, not Postgres

`build/02-architecture.md` specifies Postgres + pgvector for production. At
944 chunks a brute-force numpy dot product is sub-millisecond and needs no
service, so the demo runs anywhere with one command. The schema mirrors the
Postgres design — migrating is a rewrite of `index/store.py` alone.

Revisit if tenants pass ~200 or per-tenant corpora pass ~50k chunks.

---

## Layout

```
config.py               all settings, corpus list, attribution
ingest/frl.py           FRL OData client, ePub download, version selection
ingest/epub_parser.py   OPC-class-aware XHTML parser
ingest/austrac.py       mirror-first fetch, namespace canonicalisation
ingest/tenant.py        tenant markdown loader
ingest/chunker.py       structure-aware chunking
index/embed.py          pluggable embeddings (OpenAI / Voyage)
index/store.py          SQLite + FTS5 + numpy; tenant isolation lives here
index/build.py          build pipeline
query/retrieve.py       hybrid + RRF + rerank + definitions + crossrefs
query/answer.py         refusal rules, generation, citation validation
app/main.py             FastAPI
app/static/index.html   single-file UI
eval/                   golden set + harness
data/tenants/           synthetic demo tenants
```

---

## Performance notes, measured

Local embedding of the full 944-chunk corpus, single-threaded, batch size 8:

| Machine | Time | Peak RSS |
| --- | --- | --- |
| 2-vCPU cloud container | ~8 min | 273 MB |
| M-series MacBook (expected, 3–6× faster) | ~2–3 min | similar |

One-off. Subsequent runs reuse `data/vectors.npy`.

**Watch the batch size.** fastembed defaults to `batch_size=256` with parallel
workers. On these chunk lengths that peaked at **7.7 GB RSS and was
OOM-killed** on an 8 GB machine. `LOCAL_EMBED_BATCH=8` and `parallel=1` keeps
it flat at ~270 MB — a 28× reduction for no meaningful loss in throughput on a
small corpus. Raise it if you have RAM to spare.

## Known gaps — say these out loud rather than being caught

- **One segment.** Accountants. The legislation is shared across all five;
  adding a sector is days.
- **No TCSP starter kit exists.** AUSTRAC published five — legal, accounting,
  real estate, conveyancing, jewellery. Not a bug; the assistant should say so.
- **AUSTRAC's CC version is unstated.** Their copyright page says "a Creative
  Commons licence" with no version or deed link. Get it in writing before
  production.
- **No update pipeline.** Production needs the daily FRL poll, diff, and
  regression run described in `build/01-corpus-sources.md` §8.
- **No auth, no audit log, no portal integration.** All production concerns.

---

## Attribution

> Sourced from the Federal Register of Legislation. For the latest
> information on Australian Government law please go to
> https://www.legislation.gov.au

> © AUSTRAC for the Commonwealth of Australia 2019

AUSTRAC's licence carries a no-endorsement condition, so the UI uses no
AUSTRAC branding and labels answers as unofficial.

Meridian Accounting Partners and Corvid Advisory are fictional. Their
documents were written for this demo, contain no real client data, and are
not derived from any published template.
