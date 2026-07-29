# Getting Started — you have Groq, Voyage and Cohere keys

Follow this top to bottom. Total time ~15 minutes, most of it the index build.

---

## 0. One naming check first

**Groq ≠ Grok.**

- **Groq** — inference company, keys start `gsk_`, from https://console.groq.com/keys. This is the one you want.
- **Grok** — xAI's model, keys start `xai-`. Different company, not free, not wired into this repo.

If your key starts `gsk_` you're fine. If it starts `xai-`, go get a Groq key — it's a GitHub login and no card.

---

## 1. Install

```bash
cd "path/to/Compliance/demo"
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` now installs `groq`, `voyageai` and `cohere` since you have
all three keys. `fastembed` is still there as the no-key fallback.

---

## 2. Write your `.env`

```bash
cp .env.example .env
```

Then edit it to exactly this — you have better options than the defaults:

```bash
# generation
LLM_PROVIDER=groq
GROQ_API_KEY=gsk_your_key_here
GROQ_MODEL=llama-3.3-70b-versatile

# embeddings — start with local. See the note below before choosing voyage.
EMBED_PROVIDER=local
LOCAL_EMBED_MODEL=BAAI/bge-small-en-v1.5

# VOYAGE_API_KEY=pa-your_key_here
# VOYAGE_MODEL=voyage-4-lite

# reranker — usually the single biggest retrieval-quality jump
COHERE_API_KEY=your_key_here
COHERE_RERANK_MODEL=rerank-english-v3.0

DEMO_SEGMENT=accountant
MIN_RERANK_SCORE=0          # leave at 0 until you calibrate — see step 5b
```

**If a model name is rejected in step 3**, that's the fastest thing to fix:

| Setting | If it errors, try |
| --- | --- |
| `VOYAGE_MODEL` | `voyage-3-lite`, or `voyage-law-2` (legal-tuned, older series) |
| `COHERE_RERANK_MODEL` | `rerank-english-v3.0`, `rerank-multilingual-v3.0` |
| `GROQ_MODEL` | `openai/gpt-oss-120b`, `llama-3.1-8b-instant` |

Model names change; the preflight tells you immediately which one is wrong.

---

## 3. Preflight — do this before anything else

```bash
python scripts/preflight.py
```

Makes one tiny call per provider. A bad key surfaces in seconds instead of
eight minutes into a build. Expected output:

```
  [  OK  ] embeddings [voyage] REQUIRED
           voyage-4-lite, dim 1024  (2.7s)
  [  OK  ] embeddings [local] (alt, no key)
           BAAI/bge-small-en-v1.5, dim 384  (1.7s)
  [  OK  ] generation [groq] REQUIRED
           groq · llama-3.3-70b-versatile → {'ok': True}  (0.3s)
  [  OK  ] reranker [cohere] optional
           rerank-english-v3.0 | relevant 0.038 vs irrelevant 0.000 | correct doc ranked first
  [ FAIL ] index
           RuntimeError: index is empty — run: python -m index.build
```

The empty-index failure is expected before your first build. Everything else
should read OK.

**Read the reranker line carefully.** It shows a relevant and an irrelevant
document scored side by side. What matters is the *gap*, not the absolute
number — rerank scores are not comparable across models. A relevant passage
scoring 0.038 is normal for `rerank-english-v3.0`; the same passage scores
~0.9 on other rerankers. This is exactly why step 5b exists.

A bad key looks like `AuthenticationError: Invalid API Key` against the
provider that's broken — no guessing.

Required checks failing exits non-zero. A broken *optional* provider (Cohere)
only warns, because the pipeline degrades gracefully without it.

---

## 4. Build the index

```bash
python -m index.build
```

Downloads four instruments from the Federal Register of Legislation, parses
the ePub, chunks structurally, embeds, and writes `data/index.db` +
`data/vectors.npy`.

Roughly a minute of downloads (robots.txt `Crawl-delay: 10` is honoured, so
four instruments take ~40s of deliberate waiting), then embedding.

### Which embedding provider — this is not the obvious choice

| | Local (`bge-small`) | Voyage free tier |
| --- | --- | --- |
| Build time | 2–4 min on Apple Silicon | **~18 min** |
| Rate limits | none | **3 req/min, 10K tokens/min** without a card |
| Quality | good | better |
| Privacy story | **nothing leaves the machine** | hosted; data-use position unpublished |
| Cost | $0 | $0 |

**Voyage's 200M free tokens are not the constraint — the throttle is.**
Without a payment method on file you get 3 requests/minute and 10,000
tokens/minute. This corpus is ~179,000 tokens, so a full build is throttled
to about 18 minutes no matter how you batch it. Adding a card lifts the
limits to 2,000 RPM / 8M TPM and the free tokens still apply, so you would
not be charged — but you have to give them a card.

The embedding client handles the throttle correctly (token-budgeted batches,
rolling-window limiter, backoff on 429) so Voyage *works* on the free tier.
It's just slow.

**Recommendation: build with local.** It's faster here, has no quota, and
"embeddings never leave the machine" is a stronger sentence in a compliance
sales call than "we use a good embedding model." Switch to Voyage only if the
A/B in step 8 shows it clearly wins on your golden set.

Expected tail:

```
[total] 944 chunks
  act          371
  definition   303
  program       16
  rules        199
  sop           55
  ~127,647 words  ~178,705 tokens
[embed] embedding chunks...
  944 vectors, dim 1024
Index written to data/index.db
```

**Add contextual headers** once you're happy the basic build works. This
generates a one-line summary per chunk before embedding and measurably
improves retrieval on statutory text:

```bash
python -m index.build          # headers are ON by default when a
                               # generation provider is configured
python -m index.build --no-context   # skip them (faster, cheaper)
```

⚠️ Watch the Groq rate limit here — 944 chunks means ~944 calls at 30/min,
which is over 30 minutes and will hit the 1,000/day cap. For the demo, either
run `--no-context`, or add headers to a subset. Not worth the quota on a
944-chunk corpus.

---

## 5. Measure it

```bash
python -m eval.run_eval
```

Runs all 25 golden-set cases through the full pipeline and writes
`data/eval_results.json`. This is the number you quote to a prospect, so run
it before you demo.

What to look for:

| Metric | Target | If it's wrong |
| --- | --- | --- |
| Cross-tenant leaks | **0** | P0. Stop. Do not demo. |
| Refusal recall | **1.00** | A missed refusal is the entire risk of the product |
| Invalid citations | **0** | Mechanically enforced — non-zero means a bug |
| Refusal rate | **≤ 20%** | Too high → set `MIN_RERANK_SCORE=0`, then calibrate (step 5b) |
| Over-refusals | **0** | Same fix |

`python -m eval.run_eval --retrieval` skips the model entirely — useful for
tuning retrieval without burning Groq quota.

---

## 5b. Calibrate the refusal threshold (only if you use the reranker)

```bash
python scripts/calibrate_threshold.py
```

`MIN_RERANK_SCORE` refuses a question when the top reranked score is too low.
It ships **disabled (0)** on purpose: a guessed threshold is worse than none.

On `rerank-english-v3.0` a genuinely relevant statutory passage scores around
**0.04**. An earlier version of this repo defaulted to **0.28** — which would
have refused essentially every question in your demo. That is the
over-refusal failure mode: an assistant that refuses everything gets switched
off in a week, and the client concludes AI doesn't work.

The script runs all 25 golden questions, records the top score for each,
and compares two populations: questions the corpus *can* answer, and the
three that it genuinely cannot (`R03` billing, `R04` New Zealand, `E02` the
non-existent TCSP starter kit).

- **Clean separation** → it recommends a threshold in the gap.
- **Overlap** → it tells you to leave the threshold off, and explains why:
  in a narrow corpus where every passage is topically similar, a score
  threshold is a weak signal. The hard refusal rules — entity scoping, legal
  advice, out-of-jurisdiction, uncited deadlines — already catch these cases
  deterministically, which is better than a score.

Either answer is useful. Don't set the number by feel.

---

## 6. Run it

```bash
uvicorn app.main:app --port 8000
```

http://127.0.0.1:8000 — three tabs.

**Chat** — the demo surface.
**Evaluation** — the metrics from step 5, rendered.
**Corpus** — where the documents live, every instrument, searchable index.

---

## 7. Test it — the four moments, in order

These are the demo. Everything else is decoration.

**1. Citation.** Ask *"When must a threshold transaction report be given to
AUSTRAC?"* → answer cites a section. Click the citation chip. The actual text
of the Act opens with its compilation number and in-force date.

**2. Refusal.** Ask *"Am I a reporting entity?"* → it refuses, surfaces the
criteria, and produces a pre-researched escalation packet. Spend the most time
rehearsing this one; it's what separates you from a weekend chatbot.

**3. Their documents.** Client = Meridian. Ask *"What does our SOP say about
onboarding a trust client?"* → cites **SOP 14**, not the Act.

**4. Isolation.** Switch client to Corvid Advisory. Ask the same question.
Meridian's SOP is invisible. Enforced in SQL, not in the prompt.

Then work through the rest of the 25-question script in `eval/golden.yaml`.
Know which ones are shaky before a prospect finds them.

---

## 8. Worth doing now that you have two embedding providers

You can A/B the embeddings, which almost nobody bothers to do:

```bash
# baseline
EMBED_PROVIDER=voyage python -m index.build && python -m eval.run_eval --retrieval
cp data/eval_results.json data/eval_voyage.json

# comparison
EMBED_PROVIDER=local  python -m index.build && python -m eval.run_eval --retrieval
cp data/eval_results.json data/eval_local.json
```

Compare `mean_retrieved` and the top hit per case. If Voyage isn't clearly
better on this corpus, run the demo on local embeddings — because "nothing
leaves the machine" is a stronger sentence in a compliance sales call than
"we use a good embedding model."

That is a real decision, not a benchmark exercise. Let the data pick.

---

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `AuthenticationError: Invalid API Key` | wrong key or wrong provider | Check `gsk_` prefix for Groq |
| `model not found` | model name moved | See the table in step 2 |
| Build hangs at `[embed]` | local model on a slow CPU | Progress prints every batch — let it run |
| `RateLimitError` from Voyage | free tier is 3 RPM / 10K TPM without a card | Use `EMBED_PROVIDER=local`, or add a payment method (free tokens still apply) |
| Killed during build | OOM (was 7.7 GB before the fix) | `LOCAL_EMBED_BATCH=4` in `.env` |
| Everything refuses | `MIN_RERANK_SCORE` set by guesswork | Set it to `0`, then run `python scripts/calibrate_threshold.py` |
| `429` from Groq mid-demo | 30 req/min free tier | Wait 60s. Pre-run the demo script so answers are warm. |
| AUSTRAC pages all fail | their site blocks many hosts | Expected. Save pages into `data/austrac_manual/`. Build never blocks on it. |
| Empty index | build never completed | `python scripts/preflight.py` will say so |

---

## Before you demo to anyone

- [ ] `python scripts/preflight.py` — all required checks pass
- [ ] `python -m eval.run_eval` — 0 leaks, refusal recall 1.00, 0 invalid citations,
      refusal rate ≤ 20%
- [ ] `python scripts/calibrate_threshold.py` if the reranker is on
- [ ] Walk the 4 moments end to end without a terminal open
- [ ] Read `demo-spec.md` §8 — the 16 competitors already shipping an AI
      assistant, and why your differentiation is moments 2 and 3, not the chat box
- [ ] Have the answer ready for *"does this train on our data?"* — embeddings
      are Voyage (get their position in writing before client documents),
      generation is Groq, whose Services Agreement §4.2 bars training on
      inputs and outputs
