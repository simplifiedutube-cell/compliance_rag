"""Federal Register of Legislation client.

Public OData v4 API, no key required. robots.txt allows everything we touch
with Crawl-delay: 10, which this module honours.

Licence: legislation.gov.au content is CC BY 4.0 including commercial use.
Attribution is rendered in the UI (see config.FRL_ATTRIBUTION).
"""
from __future__ import annotations

import time
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import httpx

import config

_last_request = 0.0


def _throttle() -> None:
    """Honour robots.txt Crawl-delay: 10."""
    global _last_request
    wait = config.FRL_CRAWL_DELAY - (time.time() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.time()


@dataclass
class Version:
    title_id: str
    register_id: str
    compilation_number: str | None
    start: str              # ISO date, e.g. "2026-07-01"
    retrospective_start: str
    end: str | None
    registered_at: str
    name: str = ""

    @property
    def citation_prefix(self) -> str:
        """Stable citation key prefix, e.g. 'C2006A00169@62'.

        FRL section anchors are Word bookmarks regenerated on every
        compilation (verified: 0 of 319 survived C60 -> C62), so we mint
        our own keys from the title id, compilation number and section
        number rather than deep-linking to an anchor.
        """
        comp = self.compilation_number or "asmade"
        return f"{self.title_id}@{comp}"

    @property
    def source_url(self) -> str:
        return f"{config.FRL_WEB}/{self.title_id}/latest/text"


def _api(path: str, params: dict | None = None) -> dict:
    _throttle()
    with httpx.Client(timeout=90, follow_redirects=True) as c:
        r = c.get(f"{config.FRL_API}{path}", params=params,
                  headers={"Accept": "application/json"})
        r.raise_for_status()
        return r.json()


def get_title(title_id: str) -> dict:
    return _api(f"/titles/{title_id}")


def latest_version(title_id: str, as_of: str | None = None) -> Version:
    """The version in force on `as_of` (default: today).

    Two traps the API sets:

    1. It returns a trailing placeholder row for the instrument's sunset or
       repeal date -- start far in the future, no end, and **registerId of
       None**. Ordering by `start desc` puts that row first, so naively
       taking the newest row asks for a 2031/2036 compilation and gets a 404.
    2. Filtering on `name` returns HTTP 500, and Default.Search chokes on
       hyphens. Filter on ids and dates only.
    """
    from datetime import date

    today = as_of or date.today().isoformat()
    data = _api("/versions", {
        "$filter": f"titleId eq '{title_id}'",
        "$orderby": "start desc",
        "$top": "20",
    })
    rows = [r for r in data.get("value", []) if r.get("registerId")]
    if not rows:
        raise RuntimeError(f"No usable versions returned for {title_id}")

    def in_force(r: dict) -> bool:
        start = (r.get("start") or "")[:10]
        end = (r.get("end") or "")[:10]
        return bool(start) and start <= today and (not end or end > today)

    # In force today; otherwise the most recent one that has already started.
    row = next((r for r in rows if in_force(r)), None)
    if row is None:
        started = [r for r in rows if (r.get("start") or "")[:10] <= today]
        row = started[0] if started else rows[-1]
    title = get_title(title_id)
    return Version(
        title_id=title_id,
        register_id=row["registerId"],
        compilation_number=row.get("compilationNumber"),
        start=row["start"][:10],
        retrospective_start=(row.get("retrospectiveStart") or row["start"])[:10],
        end=(row["end"][:10] if row.get("end") else None),
        registered_at=row.get("registeredAt", ""),
        name=title.get("name", title_id),
    )


def download_epub(v: Version, cache_dir: Path | None = None) -> bytes:
    """Fetch the ePub and return the primary XHTML document.

    The ePub is the right source: its XHTML carries the Office of
    Parliamentary Counsel drafting classes (ActHead5, CharSectno,
    subsection, Definition, ...) which give us section-accurate chunking.
    There is no XML format; the PDF would mean regex-guessing structure.
    """
    cache_dir = cache_dir or config.CACHE
    cached = cache_dir / f"{v.register_id}.html"
    if cached.exists():
        return cached.read_bytes()

    url = (f"{config.FRL_WEB}/{v.title_id}/{v.start}/"
           f"{v.retrospective_start}/text/original/epub")
    _throttle()
    with httpx.Client(timeout=300, follow_redirects=True) as c:
        r = c.get(url)
        r.raise_for_status()
        if "epub" not in r.headers.get("content-type", ""):
            raise RuntimeError(
                f"Expected epub for {v.title_id}, got "
                f"{r.headers.get('content-type')} — check the date segments")
        payload = r.content

    with zipfile.ZipFile(BytesIO(payload)) as z:
        html_names = [n for n in z.namelist()
                      if n.lower().endswith((".html", ".xhtml"))
                      and "nav" not in n.lower()]
        if not html_names:
            raise RuntimeError(f"No XHTML inside ePub for {v.title_id}")
        # The body document is the largest file in the archive.
        primary = max(html_names, key=lambda n: z.getinfo(n).file_size)
        doc = z.read(primary)

    cached.write_bytes(doc)
    return doc


def fetch_instrument(title_id: str) -> tuple[Version, bytes]:
    v = latest_version(title_id)
    return v, download_epub(v)
