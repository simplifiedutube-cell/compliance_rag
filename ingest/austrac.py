"""AUSTRAC guidance ingestion.

Licence: austrac.gov.au/copyright grants a Creative Commons licence to copy,
adapt, modify, transmit and distribute AUSTRAC material, with attribution
(config.AUSTRAC_ATTRIBUTION) and one condition — usage must not suggest
AUSTRAC endorses the product. The UI therefore carries no AUSTRAC branding
and labels answers as unofficial.

Reliability: austrac.gov.au is intermittently unreachable (observed ~1 in 4
fetches succeeding from cloud egress). This module is mirror-first: it
retries with backoff, caches everything to disk, and records failures in a
log rather than silently skipping content.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from lxml import html as LH

import config
from ingest.epub_parser import norm

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

DROP_TAGS = {"script", "style", "nav", "header", "footer", "form", "svg",
             "noscript", "aside", "button"}
DROP_HINTS = ("nav", "menu", "breadcrumb", "footer", "header", "sidebar",
              "skip", "cookie", "banner", "search", "social", "related-links")


@dataclass
class Fetched:
    url: str
    canonical: str
    ok: bool
    path: Path | None
    error: str = ""


def canonicalise(url: str) -> str:
    """Collapse the six live AUSTRAC namespaces onto one canonical form.

    Both /reforms/sector-specific-guidance/... and
    /industry-and-business/obligations-and-guidance/program-starter-kits/...
    resolve to the same starter kits. Without this the whole kit gets
    double-indexed.
    """
    u = url.split("#")[0].rstrip("/")
    u = u.replace("://austrac.gov.au", "://www.austrac.gov.au")
    for src, dst in config.AUSTRAC_NAMESPACE_ALIASES:
        if src in u:
            u = u.replace(src, dst)
    return u


def _cache_path(url: str) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", url.lower().split("austrac.gov.au")[-1])
    return config.CACHE / f"austrac{slug[:120]}.html"


# Circuit breaker. If AUSTRAC refuses the first few pages, it is blocking
# this host and every remaining page will fail identically. Without this,
# 9 pages x 5 attempts x 90s timeout is over an hour before the build gives
# up -- which looks like a hang, not a failure.
_consecutive_failures = 0


def reset_circuit() -> None:
    global _consecutive_failures
    _consecutive_failures = 0


def fetch(path_or_url: str, *, attempts: int | None = None) -> Fetched:
    global _consecutive_failures

    url = (path_or_url if path_or_url.startswith("http")
           else config.AUSTRAC_BASE + path_or_url)
    canon = canonicalise(url)
    cached = _cache_path(canon)
    if cached.exists() and cached.stat().st_size > 500:
        return Fetched(url, canon, True, cached)

    if _consecutive_failures >= config.AUSTRAC_CIRCUIT_BREAK:
        return Fetched(url, canon, False, None,
                       f"skipped — AUSTRAC unreachable from this host "
                       f"({_consecutive_failures} consecutive failures)")

    attempts = attempts or config.AUSTRAC_ATTEMPTS
    last = ""
    for i in range(attempts):
        try:
            with httpx.Client(timeout=config.AUSTRAC_TIMEOUT,
                              follow_redirects=True,
                              headers={"User-Agent": UA}) as c:
                r = c.get(url)
                r.raise_for_status()
                cached.write_bytes(r.content)
                _consecutive_failures = 0
                return Fetched(url, canon, True, cached)
        except Exception as e:                      # noqa: BLE001
            last = f"{type(e).__name__}: {e}"[:200]
            if i < attempts - 1:
                time.sleep(min(2 ** i, 4))
    _consecutive_failures += 1
    return Fetched(url, canon, False, None, last)


def _clean(tree) -> None:
    for el in tree.iter():
        if el.tag in DROP_TAGS:
            el.getparent().remove(el) if el.getparent() is not None else None
    for el in tree.xpath("//*[@class or @id or @role]"):
        blob = " ".join(filter(None, [el.get("class", ""), el.get("id", ""),
                                      el.get("role", "")])).lower()
        if any(h in blob for h in DROP_HINTS):
            p = el.getparent()
            if p is not None:
                p.remove(el)


def extract_blocks(html_bytes: bytes, title_fallback: str
                   ) -> list[tuple[list[str], str]]:
    """Split a guidance page into (heading_path, text) blocks at h2/h3."""
    tree = LH.fromstring(html_bytes)
    _clean(tree)

    main = tree.xpath("//main") or tree.xpath("//*[@role='main']") or [tree]
    main = main[0]

    h1 = main.xpath(".//h1")
    page_title = norm(h1[0].text_content()) if h1 else title_fallback

    blocks: list[tuple[list[str], str]] = []
    path = [page_title]
    buf: list[str] = []

    def flush():
        text = "\n".join(x for x in buf if x).strip()
        if len(text) > 120:
            blocks.append(([*path], text))
        buf.clear()

    for el in main.iter():
        tag = str(el.tag).lower()
        if tag in ("h2", "h3"):
            flush()
            heading = norm(el.text_content())
            if not heading:
                continue
            path = ([page_title, heading] if tag == "h2"
                    else [*path[:2], heading])
        elif tag in ("p", "li", "td", "th", "dd", "dt"):
            t = norm(el.text_content())
            if t and len(t) > 2:
                buf.append(t)
    flush()
    return blocks


def ingest_pages(paths: list[str]) -> tuple[list[dict], list[Fetched]]:
    """Fetch and parse AUSTRAC pages. Returns (documents, failures)."""
    docs: list[dict] = []
    failures: list[Fetched] = []
    seen_hashes: set[int] = set()

    for p in paths:
        print(f"    fetching {p[:70]}", end="\r", flush=True)
        res = fetch(p)
        if not res.ok or res.path is None:
            failures.append(res)
            continue
        raw = res.path.read_bytes()
        title = p.rstrip("/").split("/")[-1].replace("-", " ").title()
        blocks = extract_blocks(raw, title)
        # Content-hash dedup as a backstop against namespace duplicates.
        keep = []
        for path_, text in blocks:
            h = hash(text[:400])
            if h in seen_hashes:
                continue
            seen_hashes.add(h)
            keep.append((path_, text))
        if keep:
            docs.append({"url": res.canonical, "blocks": keep})

    print(" " * 90, end="\r")          # clear the progress line

    log = config.CACHE / "austrac_fetch_log.json"
    log.write_text(json.dumps(
        [{"url": f.url, "ok": f.ok, "error": f.error}
         for f in failures], indent=2))
    return docs, failures
