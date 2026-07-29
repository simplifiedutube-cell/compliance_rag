"""Structure-aware chunking.

Never split mid-subsection. A deadline in one chunk and its exemption in
another is the failure mode that makes a compliance assistant dangerous.

Every chunk carries its full heading path, so a subsection retrieved on its
own still says which Part, Division and Section it belongs to.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field

import config
from ingest.epub_parser import Node

_SUBSEC = re.compile(r"^\(\s*(\d+[A-Z]?|[a-z]{1,3}|[ivxl]{1,6})\s*\)")


def est_tokens(text: str) -> int:
    """Cheap token estimate. Legal English runs ~1.4 tokens/word."""
    return int(len(text.split()) * 1.4) + 1


@dataclass
class Chunk:
    citation_key: str
    doc_id: str
    doc_type: str            # act | rules | guidance | sop | program | definition
    instrument_id: str | None
    compilation_id: str | None
    compilation_no: str | None
    section_no: str | None
    heading_path: list[str]
    content: str             # raw text, for display
    source_url: str
    licence: str
    effective_from: str | None = None
    effective_to: str | None = None
    segments: list[str] = field(default_factory=list)
    tenant_id: str | None = None
    defined_term: str | None = None
    cross_refs: list[str] = field(default_factory=list)
    context_header: str = ""

    @property
    def embed_text(self) -> str:
        """What actually gets embedded: heading path + context + body.

        The heading path alone is a large retrieval win — it tells the
        embedding what a bare 'the entity must...' subsection is about.
        """
        parts = [" > ".join(self.heading_path)]
        if self.context_header:
            parts.append(self.context_header)
        parts.append(self.content)
        return "\n".join(p for p in parts if p)

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode()).hexdigest()[:16]

    def to_row(self) -> dict:
        d = asdict(self)
        d.pop("context_header", None)
        return d


def _split_body(lines: list[str], max_tokens: int) -> list[list[str]]:
    """Group body lines into runs under max_tokens, breaking only at
    subsection/paragraph starts."""
    groups: list[list[str]] = []
    cur: list[str] = []
    cur_tok = 0

    for line in lines:
        t = est_tokens(line)
        starts_unit = bool(_SUBSEC.match(line))
        if cur and cur_tok + t > max_tokens and starts_unit:
            groups.append(cur)
            cur, cur_tok = [], 0
        cur.append(line)
        cur_tok += t

    if cur:
        groups.append(cur)
    return groups or [[]]


def chunk_legislation(
    nodes: list[Node],
    *,
    doc_id: str,
    doc_type: str,
    instrument_id: str,
    compilation_id: str,
    compilation_no: str | None,
    citation_prefix: str,
    source_url: str,
    effective_from: str,
    effective_to: str | None,
) -> list[Chunk]:
    out: list[Chunk] = []

    for n in nodes:
        groups = _split_body(n.body, config.CHUNK_MAX_TOKENS)
        multi = len(groups) > 1
        for i, g in enumerate(groups, start=1):
            text = "\n".join(g).strip()
            if not text:
                continue
            key = f"{citation_prefix}#s{n.section_no or 'x'}"
            if multi:
                key += f".{i}"
            out.append(Chunk(
                citation_key=key,
                doc_id=doc_id,
                doc_type=doc_type,
                instrument_id=instrument_id,
                compilation_id=compilation_id,
                compilation_no=compilation_no,
                section_no=n.section_no,
                heading_path=n.heading_path,
                content=text,
                source_url=source_url,
                licence="CC-BY-4.0",
                effective_from=effective_from,
                effective_to=effective_to,
                cross_refs=n.cross_refs(),
            ))
    return out


def chunk_definitions(
    nodes: list[Node],
    *,
    doc_id: str,
    instrument_id: str,
    compilation_id: str,
    compilation_no: str | None,
    citation_prefix: str,
    source_url: str,
    effective_from: str,
    effective_to: str | None,
) -> list[Chunk]:
    """Definitions get their own retrievable units.

    Australian AML/CTF law is definition-dense and the statutory meanings
    differ from ordinary usage ('designated service', 'threshold
    transaction', 'precious metal'). A user asking 'do I need to report
    this?' never says 'threshold transaction', but that definition decides
    the answer.
    """
    out: list[Chunk] = []
    seen: set[str] = set()

    for n in nodes:
        term = (n.defined_term or "").strip()
        if not term or term.lower() in seen:
            continue
        seen.add(term.lower())
        slug = re.sub(r"[^a-z0-9]+", "-", term.lower()).strip("-")[:48]
        out.append(Chunk(
            citation_key=f"{citation_prefix}#def-{slug}",
            doc_id=doc_id,
            doc_type="definition",
            instrument_id=instrument_id,
            compilation_id=compilation_id,
            compilation_no=compilation_no,
            section_no=n.section_no,
            heading_path=[*n.heading_path, f"Definition: {term}"],
            content=n.text,
            source_url=source_url,
            licence="CC-BY-4.0",
            effective_from=effective_from,
            effective_to=effective_to,
            defined_term=term,
        ))
    return out


def chunk_prose(
    blocks: list[tuple[list[str], str]],
    *,
    doc_id: str,
    doc_type: str,
    source_url: str,
    licence: str,
    tenant_id: str | None = None,
    segments: list[str] | None = None,
    citation_prefix: str = "",
    effective_from: str | None = None,
) -> list[Chunk]:
    """Chunk heading-delimited prose (AUSTRAC HTML, tenant SOPs).

    `blocks` is [(heading_path, text), ...] in document order.
    """
    out: list[Chunk] = []
    for idx, (path, text) in enumerate(blocks, start=1):
        text = text.strip()
        if not text:
            continue
        paras = [p for p in text.split("\n") if p.strip()]
        for j, group in enumerate(_split_body(paras, config.CHUNK_MAX_TOKENS),
                                  start=1):
            body = "\n".join(group).strip()
            if not body:
                continue
            key = f"{citation_prefix or doc_id}#b{idx}"
            if j > 1:
                key += f".{j}"
            out.append(Chunk(
                citation_key=key,
                doc_id=doc_id,
                doc_type=doc_type,
                instrument_id=None,
                compilation_id=None,
                compilation_no=None,
                section_no=None,
                heading_path=path,
                content=body,
                source_url=source_url,
                licence=licence,
                tenant_id=tenant_id,
                segments=segments or [],
                effective_from=effective_from,
            ))
    return out
