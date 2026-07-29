"""Cite-or-refuse generation with mechanical citation validation.

Asking a model nicely to cite its sources produces compliance most of the
time. In a compliance product "most of the time" is the problem. So the
output is a schema with a required citations field, and any claim citing
something that was not retrieved is rejected before the user sees it.

That turns "please cite your sources" from a hope into an invariant.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import config
from query import llm
from query.retrieve import Hit, retrieve

# --------------------------------------------------------------- hard rules

ENTITY_SCOPING = [
    r"\bam i (a |an )?(reporting entity|regulated|captured|caught)",
    r"\bdo(es)? (the|these|this|it|aml|amlctf|tranche)?\s*\w*\s*(law|act|rules|"
    r"regime|obligations?|legislation)?\s*(apply|cover|catch)\b.*\b(me|us|my|our)\b",
    r"\bare we (a |an )?(reporting entity|captured|regulated|caught)",
    r"\bdo i need to (enrol|enroll|register) (with )?austrac",
    r"\bam i (captured|covered|in scope|exempt)",
]
LEGAL_ADVICE = [
    r"\bshould i (report|lodge|file|tell|disclose)\b",
    r"\bis (this|that|it) (transaction |matter |activity )?suspicious\b",
    r"\bwhat should i do about my client\b",
    r"\bcan i (be|get) (prosecuted|fined|penalised)\b",
]
OUT_OF_SCOPE = [
    r"\bnew zealand\b|\bnz\b|\buk\b|\bunited kingdom\b|\bsingapore\b|\busa?\b|"
    r"\bunited states\b|\bfatca\b|\bcanada\b",
]
DEADLINE_Q = [
    r"\bhow (long|many days|soon)\b", r"\bwhen (must|do|does|is|are)\b",
    r"\bdeadline\b", r"\btime ?frame\b", r"\bwithin how\b", r"\bdue\b",
]

REFUSALS = {
    "entity_scoping": (
        "I can't tell you whether you're a reporting entity. That's the "
        "highest-stakes determination under the Act and it turns on the "
        "specific services you provide, so it needs a human with your full "
        "circumstances in front of them. Here are the criteria that apply "
        "and the sections that set them out."),
    "legal_advice": (
        "That's a judgement call about your specific situation rather than "
        "a question about what the rules say, so I won't answer it. Here's "
        "the relevant guidance, and I've prepared an escalation with the "
        "sections already pulled."),
    "out_of_scope": (
        "My corpus covers Australian AML/CTF law only — the AML/CTF Act "
        "2006, the AML/CTF Rules 2025 and related instruments, plus your "
        "own compliance documents. I can't answer for other jurisdictions."),
    "low_confidence": (
        "I couldn't find a passage that clearly answers this, so I'd rather "
        "escalate than guess. Here's the closest material I found."),
    "uncited_deadline": (
        "This asks about a reporting deadline and I couldn't ground the "
        "answer in a specific provision. Deadlines under the Act are "
        "reportable obligations, so I won't estimate one."),
}

SYSTEM = """You are a compliance knowledge assistant for {segment}s subject \
to Australia's AML/CTF regime (Tranche 2, commenced 1 July 2026).

You answer ONLY from the numbered sources provided. You are not a lawyer and \
you do not give legal advice.

Rules, in order of importance:
1. Every factual claim MUST cite at least one source by its exact citation_key.
2. If the sources do not answer the question, set answer_type to "refused". \
Do not reason from general knowledge about AML.
3. Never state a reporting deadline, threshold, or retention period unless a \
source states it. Quote the source's own figure.
4. Never tell the user whether they are a reporting entity.
5. Where a source is the user's own document (marked TENANT), say so — they \
care about the difference between the law and their own procedure.
6. Prefer the user's own documents for questions about internal process, and \
legislation for questions about obligations. If they conflict, say so.

Respond with JSON only:
{{
  "answer_type": "answered" | "partial" | "refused",
  "summary": "one sentence, plain English",
  "claims": [{{"text": "...", "citations": ["citation_key"]}}],
  "caveats": ["..."],
  "escalate": true | false,
  "escalation_reason": "..." or null
}}"""

USER = """Question: {question}

Sources:
{sources}

Answer with JSON only."""


@dataclass
class Claim:
    text: str
    citations: list[str]


@dataclass
class Answer:
    answer_type: str
    summary: str
    claims: list[Claim] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    escalate: bool = False
    escalation_reason: str | None = None
    hits: list[Hit] = field(default_factory=list)
    corpus_version: str = ""
    diagnostics: dict = field(default_factory=dict)
    validation: dict = field(default_factory=dict)

    @property
    def refused(self) -> bool:
        return self.answer_type == "refused"


def _matches(patterns: list[str], q: str) -> bool:
    return any(re.search(p, q, re.I) for p in patterns)


def _corpus_version(hits: list[Hit]) -> str:
    parts = []
    for h in hits:
        if h.instrument_id == "C2006A00169" and h.compilation_no:
            parts.append(f"AML/CTF Act compilation {h.compilation_no}")
        elif h.instrument_id == "F2025L01026":
            parts.append("AML/CTF Rules 2025")
    uniq = list(dict.fromkeys(parts))
    return ("Based on " + ", ".join(uniq) + ", in force 1 July 2026"
            if uniq else "Based on the indexed corpus")


def _format_sources(hits: list[Hit]) -> str:
    out = []
    for i, h in enumerate(hits, 1):
        tag = "TENANT" if h.tenant_id else h.doc_type.upper()
        out.append(
            f"[{i}] citation_key: {h.citation_key}  ({tag})\n"
            f"    location: {h.label}\n"
            f"    text: {h.content[:2600]}")
    return "\n\n".join(out)


def _refuse(kind: str, hits: list[Hit], diag: dict) -> Answer:
    return Answer(
        answer_type="refused",
        summary=REFUSALS[kind],
        escalate=True,
        escalation_reason=kind,
        hits=hits,
        corpus_version=_corpus_version(hits),
        diagnostics=diag,
        validation={"rule": kind},
    )


def _call_model(question: str, hits: list[Hit], segment: str) -> dict:
    return llm.generate_json(
        SYSTEM.format(segment=segment),
        USER.format(question=question, sources=_format_sources(hits)),
    )


def _validate(payload: dict, hits: list[Hit]) -> tuple[list[Claim], dict]:
    """Reject any citation that was not retrieved for this query.

    This catches fabricated citations. It does NOT catch a real citation
    attached to a claim it does not support — that needs human sampling.
    See build/03-evaluation.md.
    """
    allowed = {h.citation_key for h in hits}
    good: list[Claim] = []
    bad_keys: list[str] = []
    uncited = 0

    for c in payload.get("claims", []):
        text = (c.get("text") or "").strip()
        if not text:
            continue
        cites = [k for k in (c.get("citations") or []) if k in allowed]
        bad_keys += [k for k in (c.get("citations") or []) if k not in allowed]
        if not cites:
            uncited += 1
            continue
        good.append(Claim(text=text, citations=cites))

    return good, {
        "claims_in": len(payload.get("claims", [])),
        "claims_kept": len(good),
        "uncited_dropped": uncited,
        "invalid_citations": bad_keys,
    }


def ask(
    question: str,
    *,
    tenant_id: str | None = None,
    segment: str = config.DEMO_SEGMENT,
    as_of: str | None = None,
) -> Answer:
    q = question.strip()

    hits, diag = retrieve(q, tenant_id=tenant_id, segment=segment, as_of=as_of)

    # Hard rules run before the model. These are not left to its judgement.
    if _matches(OUT_OF_SCOPE, q):
        return _refuse("out_of_scope", hits[:4], diag)
    if _matches(ENTITY_SCOPING, q):
        return _refuse("entity_scoping", hits[:6], diag)
    if _matches(LEGAL_ADVICE, q):
        return _refuse("legal_advice", hits[:6], diag)
    if not hits:
        return _refuse("low_confidence", [], diag)
    if diag.get("top_score", 0) and config.COHERE_API_KEY and \
            diag["top_score"] < config.MIN_RERANK_SCORE:
        return _refuse("low_confidence", hits[:4], diag)

    if not llm.available():
        return Answer(
            answer_type="partial",
            summary=(f"No generation provider configured "
                     f"(LLM_PROVIDER={config.LLM_PROVIDER}) — showing the "
                     f"retrieved sources without a generated answer. "
                     f"Retrieval, refusal rules and isolation all still work."),
            hits=hits, diagnostics=diag,
            corpus_version=_corpus_version(hits))

    try:
        payload = _call_model(q, hits, segment)
    except Exception as e:                           # noqa: BLE001
        a = _refuse("low_confidence", hits[:5], diag)
        a.validation["error"] = f"{type(e).__name__}: {e}"[:200]
        return a

    claims, validation = _validate(payload, hits)

    answer_type = payload.get("answer_type", "answered")
    if not claims and answer_type != "refused":
        answer_type = "refused"

    # A deadline question that produced no grounded claim fails closed.
    if _matches(DEADLINE_Q, q) and not claims:
        a = _refuse("uncited_deadline", hits[:5], diag)
        a.validation = validation
        return a

    return Answer(
        answer_type=answer_type,
        summary=(payload.get("summary")
                 or REFUSALS["low_confidence"]).strip(),
        claims=claims,
        caveats=[c for c in payload.get("caveats", []) if c],
        escalate=bool(payload.get("escalate")) or answer_type == "refused",
        escalation_reason=payload.get("escalation_reason"),
        hits=hits,
        corpus_version=_corpus_version(hits),
        diagnostics=diag,
        validation=validation,
    )


def escalation_packet(question: str, a: Answer, tenant_id: str | None) -> str:
    """The pre-researched hand-off. This is the part that sells the product:
    the expert opens a ticket that already has the sections pulled."""
    lines = [
        "ESCALATION — AML/CTF knowledge assistant",
        f"Tenant: {tenant_id or '(shared)'}",
        f"Question: {question}",
        f"Reason: {a.escalation_reason or a.answer_type}",
        f"Corpus: {a.corpus_version}",
        "",
        "Relevant sources already retrieved:",
    ]
    for h in a.hits[:6]:
        lines += [f"  • [{h.citation_key}] {h.label}",
                  f"    {h.content[:400].strip()}", ""]
    if a.claims:
        lines.append("Draft (unverified):")
        lines += [f"  - {c.text}  {c.citations}" for c in a.claims]
    return "\n".join(lines)
