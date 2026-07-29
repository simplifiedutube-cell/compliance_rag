"""Tenant document loader.

This is the moat. Every competitor's assistant can answer from the Act.
None of them can answer "what does our SOP 14 say?"

Demo tenants are synthetic documents written for this project — no real
client data, no copied templates.
"""
from __future__ import annotations

from pathlib import Path

import config
from ingest.epub_parser import norm


def load_markdown(path: Path) -> tuple[str, list[tuple[list[str], str]]]:
    """Split a markdown doc into (heading_path, text) blocks at ## / ###."""
    lines = path.read_text(encoding="utf-8").splitlines()
    title = path.stem.replace("-", " ").replace("_", " ").title()
    blocks: list[tuple[list[str], str]] = []
    path_stack = [title]
    buf: list[str] = []

    def flush() -> None:
        text = "\n".join(x for x in buf if x.strip()).strip()
        if len(text) > 60:
            blocks.append(([*path_stack], text))
        buf.clear()

    for raw in lines:
        line = raw.rstrip()
        if line.startswith("# "):
            flush()
            title = norm(line[2:])
            path_stack = [title]
        elif line.startswith("## "):
            flush()
            path_stack = [title, norm(line[3:])]
        elif line.startswith("### "):
            flush()
            path_stack = [*path_stack[:2], norm(line[4:])]
        else:
            if line.strip():
                buf.append(norm(line))
    flush()
    return title, blocks


def load_tenant(tenant_id: str) -> list[dict]:
    """Load every markdown document for a tenant."""
    root = config.TENANT_DIR / tenant_id
    if not root.exists():
        return []
    out = []
    for f in sorted(root.glob("*.md")):
        title, blocks = load_markdown(f)
        if blocks:
            out.append({
                "doc_id": f"{tenant_id}/{f.stem}",
                "title": title,
                "blocks": blocks,
                "doc_type": "program" if "program" in f.stem.lower() else "sop",
            })
    return out


def list_tenants() -> list[str]:
    return sorted(p.name for p in config.TENANT_DIR.iterdir() if p.is_dir())
