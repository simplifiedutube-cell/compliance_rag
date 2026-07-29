"""Structure-aware parser for Federal Register of Legislation ePub XHTML.

Validated against AML/CTF Act 2006 compilation 62 (C2026C00274):
    332 ActHead5 (sections)   332 CharSectno   260 Definition
     22 ActHead2 (Parts)       73 ActHead3 (Divisions)
   1390 ENoteTableText (endnotes -- dropped)

The document is split across several <div> wrappers, so we flatten in
document order rather than walking direct children.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from lxml import html as LH

# Heading levels, outermost first.
HEADINGS = {
    "ActHead1": "chapter",
    "ActHead2": "part",
    "ActHead3": "division",
    "ActHead4": "subdivision",
    "ActHead5": "section",
}
LEVEL_ORDER = ["chapter", "part", "division", "subdivision", "section"]

# Body content we keep.
BODY = {
    "subsection", "subsection2", "paragraph", "paragraphsub",
    "SubsectionHead", "Penalty", "notetext", "Definition",
    "Tabletext", "TableHeading", "Tablea", "Tablei", "SOBullet", "SOPara",
}

# Noise: endnotes (~1390 rows, roughly 100 PDF pages) and the table of
# contents. Dropping these removes ~25% of the Act's tokens and a large
# amount of retrieval noise.
DROP = {"ENoteTableText", "ENoteTableHeading", "ENotesHeading1",
        "ENotesHeading2", "Header", "notemargin"} | {f"TOC{i}" for i in range(1, 6)}

_WS = re.compile(r"[ \t]+")
_SECT_REF = re.compile(r"\bsections?\s+(\d+[A-Z]{0,3})\b", re.I)


def norm(s: str) -> str:
    """Normalise drafting typography to plain text."""
    s = unicodedata.normalize("NFKC", s)
    s = (s.replace("‐", "-").replace("‑", "-").replace("–", "-").replace("—", "-")
           .replace(" ", " ").replace("’", "'").replace("“", '"')
           .replace("”", '"'))
    s = _WS.sub(" ", s)
    return s.strip()


@dataclass
class Node:
    """One structural unit of an instrument."""
    kind: str                        # 'section' | 'definition'
    section_no: str | None
    heading: str
    heading_path: list[str] = field(default_factory=list)
    body: list[str] = field(default_factory=list)
    defined_term: str | None = None

    @property
    def text(self) -> str:
        return "\n".join(x for x in self.body if x)

    def cross_refs(self) -> list[str]:
        return sorted({m.group(1) for m in _SECT_REF.finditer(self.text)})


def _heading_text(el) -> tuple[str | None, str]:
    """Split a heading into (number, title) using CharSectno / CharPartNo."""
    num = None
    for cls in ("CharSectno", "CharPartNo", "CharDivNo"):
        span = el.find(f'.//span[@class="{cls}"]')
        if span is not None:
            num = norm(span.text_content())
            break
    full = norm(el.text_content())
    if num and full.startswith(num):
        return num, norm(full[len(num):].lstrip("—-. "))
    return num, full


def _defined_term(text: str) -> str | None:
    """First phrase of a definition, e.g. 'account includes:' -> 'account'."""
    t = text.strip()
    if not t:
        return None
    m = re.match(r"^([a-z][a-zA-Z0-9''\-/ ]{1,60}?)\s+"
                 r"(means|includes|has the meaning|is |:)", t)
    if m:
        return m.group(1).strip().rstrip(",")
    head = t.split(":")[0].split(" means")[0]
    return head.strip()[:60] or None


def parse(doc_bytes: bytes) -> tuple[list[Node], list[Node]]:
    """Return (sections, definitions) in document order."""
    root = LH.fromstring(doc_bytes)
    body = root.find(".//body")
    if body is None:
        body = root

    path: dict[str, str] = {}
    sections: list[Node] = []
    definitions: list[Node] = []
    current: Node | None = None

    for el in body.iter():
        cls = el.get("class")
        if not cls or cls in DROP:
            continue

        if cls in HEADINGS:
            level = HEADINGS[cls]
            num, title = _heading_text(el)
            if level == "section":
                # 'Section 5A — Precious metal, precious stones and ...'
                label = f"Section {num} — {title}" if num else title
            else:
                # Part/Division headings already read as 'Part 1-Introduction'
                # in full, so keep the whole string.
                label = norm(el.text_content())

            # Reset deeper levels when a shallower heading appears.
            idx = LEVEL_ORDER.index(level)
            for deeper in LEVEL_ORDER[idx:]:
                path.pop(deeper, None)
            path[level] = label

            if level == "section":
                current = Node(
                    kind="section",
                    section_no=num,
                    heading=title,
                    heading_path=[path[l] for l in LEVEL_ORDER if l in path],
                )
                sections.append(current)
            else:
                current = None
            continue

        if cls not in BODY:
            continue

        text = norm(el.text_content())
        if not text:
            continue

        if cls == "Definition":
            term = _defined_term(text)
            definitions.append(Node(
                kind="definition",
                section_no=current.section_no if current else None,
                heading=term or "definition",
                heading_path=([*current.heading_path] if current
                              else [p for p in path.values()]),
                body=[text],
                defined_term=term,
            ))
            # Definitions also stay in their parent section's body so the
            # section reads coherently when retrieved whole.

        if current is not None:
            current.body.append(text)

    sections = [s for s in sections if s.text]
    return sections, definitions
