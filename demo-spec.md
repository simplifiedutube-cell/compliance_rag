# Demo Specification — Copyright-Clean Build

**Goal:** a working assistant good enough to open a sales conversation, built in ~5 days, using nothing but openly-licensed Commonwealth legislation and documents you wrote yourself.

**Not the goal:** the product. That comes after a client says yes.

---

## 1. The legal position — the whole point of this design

### What the demo indexes

**Commonwealth legislation only, from the Federal Register of Legislation.**

| Document | ID | Words |
| --- | --- | --- |
| AML/CTF Act 2006 (compilation 62) | `C2006A00169` / `C2026C00274` | 91,530 |
| AML/CTF Rules 2025 (compilation 1) | `F2025L01026` / `F2026C00274` | 46,039 |
| AML/CTF (Class Exemptions and Other Matters) Rules 2007 | `F2007L01000` | 13,020 |
| AML/CTF Transitional Rules 2026 | `F2026L00393` | 3,416 |
| | **Total** | **154,005** |

Licence, verbatim from legislation.gov.au's terms of use:

> "all content on the Federal Register of Legislation … is provided under Creative Commons Attribution 4.0 International"
> "…share … and adapt … for any purpose, **even commercially**."

Attribution required on the demo UI:

> *Sourced from the Federal Register of Legislation at [date of download]. For the latest information on Australian Government law please go to https://www.legislation.gov.au*

Access is via the public OData API and official file downloads. `robots.txt` permits it with `Crawl-delay: 10`. Four documents, four downloads. **No scraping in any meaningful sense** — you're using a published API the way it's intended.

### Plus: synthetic tenant documents you write yourself

A fictional accounting firm's compliance program and SOPs. **You author these.** They are your copyright, they contain no real client data, and they demonstrate the single most important thing about the product.

### AUSTRAC guidance — included, with conditions

**Decision (29 July 2026): AUSTRAC guidance is in.**

`austrac.gov.au/copyright` grants a Creative Commons licence to copy, adapt,
modify, transmit and distribute AUSTRAC material. Attribution required:
*"© AUSTRAC for the Commonwealth of Australia 2019"*.

Two conditions the build honours:

1. **No-endorsement.** Usage must not suggest AUSTRAC endorses the product.
   The UI therefore carries no AUSTRAC logo or branding and labels answers as
   unofficial.
2. **The CC version is not stated.** The copyright page says "a Creative
   Commons licence" with no version number and no deed link. Almost certainly
   CC BY 4.0 (the whole-of-government default), but unconfirmed. **Get this in
   writing before a paid production build** — it's a five-minute email to
   AUSTRAC, and it's cheap insurance.

**Practical caveat:** the site refuses automated requests from many hosts
(~1 in 4 succeed from cloud egress; 0 in 8 from some networks). The build
never blocks on it, and `data/austrac_manual/` takes browser-saved pages as a
fallback. See the demo README.

**Starter kits** are still held back for now. They're templates users fill in
and rely on, so reproducing their text means distributing compliance
artefacts. The licence permits it; get product sign-off on how they surface
before turning them on.

### What the demo deliberately excludes

| Source | Why excluded |
| --- | --- |
| **AustLII** | Explicitly blocks automated access and claims copyright in its markup. The same legislation is CC BY at the FRL. |
| **CPA Australia** | Terms ban scraping and commercial exploitation outright. |
| **CA ANZ** | Terms cl 19.2 specifically prohibits submitting their content to generative AI models. |
| **Law Society guides** | All rights reserved. Best resource in the market, and not yours. |
| **Law firm alerts, competitor content, paywalled press** | Copyright, plus much of it is already stale. |

### Say this out loud in the demo

This constraint is a **selling point**, not an apology:

> *"This runs on Commonwealth legislation published under Creative Commons Attribution 4.0, AUSTRAC's own guidance under their Creative Commons licence, and documents I wrote myself. Nothing scraped from a source that prohibits it — not AustLII, not CPA, not CA ANZ, whose terms specifically ban feeding their content to an AI model. When we build the real thing, the only thing added is your own documents, which never leave your control."*

Every prospect in a compliance market is risk-conscious by profession. Leading with a clean provenance story does more work than any feature.

---

## 2. Scope — one segment, five days

**Segment: accountants.** 28,223 newly captured entities, the largest of the five with mature guidance, and no LPUL exposure (unlike lawyers and conveyancers, which you should not open with).

### In scope

- Ingest 4 legislation instruments via the FRL API, ePub → structure-aware chunks
- AUSTRAC core obligations guidance + accountant sector page (mirror-first,
  with a browser-saved fallback)
- Definitions index (260 from the Act)
- Hybrid retrieval: pgvector + Postgres full-text + RRF
- Reranking
- Cite-or-refuse structured generation with mechanical citation validation
- Refusal rules: entity scoping, uncited deadlines, legal advice, out-of-corpus
- Two synthetic tenants, to demo isolation live
- Escalation preview — the draft answer plus retrieved sections, formatted as a support ticket
- Simple web UI
- 25-question demo script with known-good answers

### Out of scope

Portal integration · auth · AUSTRAC starter kits · the other four segments · update pipeline · audit logging · production hosting · analytics

Say what's out of scope in the demo. It signals you know what a real build costs.

---

## 3. Synthetic tenant documents — write these yourself

Invent **Meridian Accounting Partners Pty Ltd**, a fictional 12-person Sydney practice. Write:

| Document | Length | Purpose in the demo |
| --- | --- | --- |
| AML/CTF Program (10 sections) | ~4,000 words | Mirrors the standard structure providers sell |
| SOP 03 — Initial CDD for individual clients | ~800 words | The "answer from *my* document" moment |
| SOP 07 — Escalating unusual activity | ~700 words | Internal process, unfindable in legislation |
| SOP 11 — Record keeping and retention | ~600 words | Sits alongside a statutory rule — good contrast |
| SOP 14 — Onboarding a trust client | ~900 words | Complex, high-question-volume area |
| Risk assessment summary | ~1,200 words | Firm-specific risk ratings |

Then a **second** synthetic tenant — "Corvid Advisory," 4 documents, deliberately different content — purely so you can demonstrate isolation live.

**Write them yourself, or generate them and edit heavily.** Do not copy AUSTRAC's starter kit templates into them. The point is that they're plausibly shaped like a real client's documents, not that they're accurate compliance advice.

**Time: half a day.** It feels like busywork. It is the highest-leverage half-day in the build, because it's what produces the moment described below.

---

## 4. The demo moments

Four things to show, in this order. Everything else is decoration.

### Moment 1 — The citation

Ask: *"When do I need to lodge a threshold transaction report?"*

The answer appears with the section number, and clicking it opens the actual text of the Act.

Say: *"Every answer is traceable. Your compliance officer can verify it in ten seconds instead of trusting a black box."*

### Moment 2 — The refusal ⭐ the one that actually sells

Ask: *"I'm a bookkeeper. Am I a reporting entity?"*

The assistant **refuses**, surfaces the criteria and relevant sections, and offers escalation.

Say: *"This is the question your clients ask most and the one you can least afford to get wrong. It will never answer it. It gives your expert everything needed to answer in two minutes instead of twenty."*

Most AI demos show capability. Showing a deliberate, well-designed refusal is what separates you from someone who wired up a chatbot over a weekend. **Spend the most time making this moment land.**

### Moment 3 — Their own documents ⭐ the one that differentiates

Ask: *"What does our SOP say about onboarding a trust client?"*

Answer cites **SOP 14**, not the Act. Then ask a question that needs both, and watch it cite the SOP *and* the statutory requirement together.

Say: *"Bluon-style public assistants can index the Act. None of them know your program or your SOPs. That's the part nobody else can build for you."*

### Moment 4 — Isolation

Switch to tenant Corvid Advisory. Ask the same SOP 14 question. It doesn't exist for them.

Say: *"Tenant isolation is enforced in the database query, not in the prompt. One client can never see another's documents."*

Compliance buyers care about this more than almost anything else, and most demos never mention it.

---

## 5. Build plan — 5 days

| Day | Work | Done when |
| --- | --- | --- |
| **1** | FRL API client, ePub download and parse, structure-aware chunker, definitions extraction | 4 instruments chunked with correct section numbers and heading paths |
| **2** | Postgres + pgvector schema, embeddings, contextual headers, hybrid search + RRF, reranking | Recall@10 ≥ 0.95 on 25 hand-written test questions |
| **3** | Structured cite-or-refuse generation, citation validation, refusal rules, escalation payload | All four demo moments work end to end |
| **4** | Web UI, tenant switcher, citation click-through to source text, escalation preview panel | Demo runs cleanly without a terminal |
| **5** | Write synthetic tenant docs, ingest, rehearse the script, fix what breaks | 25-question script runs without a surprise |

Half a day of slack on day 5 is not optional. Something will break in rehearsal.

---

## 6. Demo script — 25 questions

Rehearse all of them. Know which are shaky.

**Should answer cleanly with a citation (10)**

1. When must a threshold transaction report be given to AUSTRAC?
2. What is a designated service?
3. How long must I keep customer identification records?
4. What is a suspicious matter report and when is it due?
5. Who is a beneficial owner under the Act?
6. What is required in an AML/CTF program?
7. What are the tipping-off restrictions?
8. Is gold bullion a precious metal under the Act?
9. What is ongoing customer due diligence?
10. What must a reporting entity do before providing a designated service?

**Should answer from tenant documents (5)**

11. What does our SOP say about onboarding a trust client?
12. Who do we escalate unusual activity to?
13. What's our record retention period? *(shows SOP + statutory rule together)*
14. What are our firm's high-risk client categories?
15. Which section of our program covers staff training?

**Should refuse (6)** — the important ones

16. Am I a reporting entity? → **refuse**, surface criteria
17. Is this transaction suspicious? → **refuse**, judgment call
18. Can I bill AML compliance work to my client? → **refuse**, not in corpus (real ALPMA question)
19. What does the NZ AML Act require? → **refuse**, out of jurisdiction
20. Should I report my client to AUSTRAC? → **refuse**, escalate
21. My client is a PEP from [country] — what do I do? → **refuse**, fact-specific

**Edge cases (4)** — show these if the room is technical

22. What were my obligations in June 2026? → point-in-time
23. Do the transitional rules apply to me? → `F2026L00393`, mostly not for Tranche 2
24. What's in the TCSP starter kit? → **there isn't one**; the assistant should say so
25. *(Corvid tenant)* What does our SOP say about trust clients? → isolation

Question 24 is worth including deliberately. AUSTRAC published five starter kits — legal, accounting, real estate, conveyancing, jewellery — and no TCSP kit. An assistant that correctly reports a gap in the source material rather than inventing an answer is doing exactly what a compliance buyer needs to see.

---

## 7. What could embarrass you

| Risk | Mitigation |
| --- | --- |
| AUSTRAC guidance didn't ingest (their site blocks you) | Save the pages from your browser into `data/austrac_manual/` the day before. Don't discover this in the room. |
| Prospect asks about their own sector, not accountants | *"One segment for the demo. The legislation is shared across all five; adding a sector is days, not weeks."* |
| Assistant refuses too much and looks useless | Calibrate the threshold on day 5. Track refusal rate. Target ≤ 20% on the script. |
| Live latency | Cache the 25 scripted answers. Not cheating — it's a demo. |
| *"How is this different from ComplyReady?"* | Read §8. Have the answer ready before you send the first email. |

---

## 8. Read this before you demo anything

The Cursor run found **16 competitors already shipping an AI assistant** — not the 4 I estimated from my earlier research. That's roughly **20% penetration, not 10%.**

Worse, one of them is the product:

> **ComplyReady** — *"ComplyReady AI: Answers questions from Australian AML/CTF legislation, AUSTRAC guidance and sector rules."* $62–129/month.

That is the pitch, shipped, at a price a solo consultant cannot undercut. Also live: AMLTranche's "AI Knowledge Hub" (vectorised AUSTRAC guidance, $59–149), AML Shield's "AML Guru", Veriqua's "AI Compliance Assistant", Verifia's "Risk Q&A", AML Mate, Instant Compliance, InfoTrack's "Tilly", and more.

**Three things follow.**

**Selling "an AI assistant for AML" is now a losing pitch.** It's a commodity feature and 16 providers have it. Do not open with it.

**Two things remain genuinely differentiated**, and both are visible in the demo moments above:

- *Their own documents.* Every competitor's assistant answers from public sources. None can answer "what does our SOP 14 say?" That's Moment 3, and it's the reason to build the synthetic tenant docs properly.
- *Disciplined refusal with an escalation payload.* The competitor products, on public evidence, answer questions. None advertises refusing the entity-scoping question or handing a pre-researched packet to a human. That's Moment 2, and in a domain where a wrong answer is reportable, it is the more defensible position.

**Retarget.** The 13 Tier A leads without AI are still valid, but the sharper play may now be the associations. The Cursor run found **eleven bodies with no software partner** — including the Institute of Public Accountants, Law Society of NSW, Queensland Law Society, Law Societies of WA and SA, REINSW, REIQ, ALPMA, the Australian Institute of Conveyancers, the Jewellers Association, and the Governance Institute. Large member bases, published corpora, real budgets, no incumbent.

Worth a conversation before the outreach goes out.
