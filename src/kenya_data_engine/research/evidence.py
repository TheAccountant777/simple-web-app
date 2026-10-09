"""Evidence book: fetched page and PDF text, stored per run, served to models as untrusted data."""

import asyncio
import hashlib
import io
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

import pdfplumber
from trafilatura import extract_metadata

from kenya_data_engine.context import RunContext
from kenya_data_engine.data.adapters.base import policy_fetch
from kenya_data_engine.data.store import BlobStore
from kenya_data_engine.errors import ExtractError, FetchError
from kenya_data_engine.research.models import TextEvidence
from kenya_data_engine.research.tiers import host_of, tier_for
from kenya_data_engine.tools.fetch import _extract
from kenya_data_engine.tools.pdf import _parse
from kenya_data_engine.tools.urlpolicy import sniff

_HYPHEN = re.compile(r"(\w)-\n\s*([a-z])")
_WRAP = re.compile(r"(?<![.!?:;\"'”|)\]])\n(?=[a-z])")
_TOKEN = re.compile(r"[a-z0-9]+(?:[.,][0-9]+)*")
_TAG = re.compile(r"<\s*(/?)\s*evidence", re.IGNORECASE)
_PDF_DATE = re.compile(r"D:(\d{4})(\d{2})?(\d{2})?")
GAP = "[…]"


def dehyphenate(text: str) -> str:
    """Rejoin words split at a line end and hard-wrapped lines inside a paragraph."""
    text = _HYPHEN.sub(r"\1\2", text)
    return _WRAP.sub(" ", text)


def _tokens(s: str) -> tuple[set[str], set[str]]:
    toks = _TOKEN.findall(s.lower())
    words = {t for t in toks if len(t) > 2 and not t[0].isdigit()}
    nums = {t for t in toks if t[0].isdigit()}
    return words, nums


def _pdf_meta(content: bytes) -> tuple[str | None, date | None]:
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        meta = pdf.metadata or {}
    title = str(meta["Title"]).strip() if meta.get("Title") else None
    m = _PDF_DATE.match(str(meta.get("CreationDate") or ""))
    published = None
    if m:
        try:
            published = date(int(m[1]), int(m[2] or 1), int(m[3] or 1))
        except ValueError:
            published = None
    return title, published


def _html_meta(html: str) -> date | None:
    meta = extract_metadata(html)
    raw = getattr(meta, "date", None) if meta else None
    try:
        return date.fromisoformat(str(raw)[:10]) if raw else None
    except ValueError:
        return None


class RobotsDisallowed(FetchError):
    """The final URL after redirects is disallowed by robots.txt."""


class EvidenceBook:
    def __init__(
        self, run_dir: Path, tiers: dict[str, int], redact: Callable[[str], str] | None = None
    ) -> None:
        self.dir = run_dir / "research" / "evidence"
        self._run_dir = run_dir
        self._tiers = tiers
        self._redact = redact or (lambda s: s)
        self.items: list[TextEvidence] = []
        self.seen_urls: set[str] = set()
        self._keys: dict[tuple[str, str, tuple[int, ...]], TextEvidence] = {}

    # --- adding ---------------------------------------------------------------------------

    async def add_url(
        self,
        url: str,
        ctx: RunContext,
        *,
        kind: Literal["page", "item"] = "item",
        pdf_pages: list[int] | None = None,
        final_ok: Callable[[str], Awaitable[bool]] | None = None,
    ) -> TextEvidence:
        """Fetch through the data fetch policy, store the blob and text, return the evidence."""
        res = await policy_fetch(url, ctx, kind)
        if final_ok is not None and res.url != url and not await final_ok(res.url):
            raise RobotsDisallowed("disallowed by robots.txt")  # discard before storing anything
        content = res.content
        blobs = BlobStore(ctx.home.blobs_dir, ctx.home.db_path)
        sha = blobs.put(content)
        blobs.ref(sha, f"evidence:{ctx.run.run_id}")  # gc must not reap what a brief cites
        key = (url, sha, tuple(pdf_pages or ()))
        if key in self._keys:
            return self._keys[key]
        detected = sniff(content)
        title: str | None
        published: date | None
        if detected == "pdf":
            try:
                pages = await asyncio.to_thread(_parse, content, pdf_pages)
                title, published = await asyncio.to_thread(_pdf_meta, content)
            except Exception as exc:
                raise ExtractError(f"could not parse PDF at {url}", hint=str(exc)) from exc
            text = "\n\n".join(f"[page {p.number}]\n{p.text}" for p in pages if p.text.strip())
        elif detected in ("html", "csv", "json"):
            raw = content.decode("utf-8", errors="replace")
            if detected == "html":
                text, title = await asyncio.to_thread(_extract, raw)
                published = await asyncio.to_thread(_html_meta, raw)
            else:
                text, title, published = raw.strip(), None, None
        else:
            raise ExtractError(
                f"not a text document ({detected}): {url}",
                hint="use preview_table for spreadsheets",
            )
        if not text.strip():
            raise ExtractError(f"no readable text at {url}")
        ev = self._store(url, res.url, text, title, published, sha)
        self._keys[key] = ev
        return ev

    def add_text(
        self, url: str, text: str, title: str | None, published: date | None
    ) -> TextEvidence:
        """Add already-extracted text (tests and non-HTTP sources)."""
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return self._store(url, url, text, title, published, sha)

    def _store(
        self,
        url: str,
        final_url: str,
        text: str,
        title: str | None,
        published: date | None,
        sha: str,
    ) -> TextEvidence:
        n = len(self.items) + 1
        label = f"E{n}"
        url, final_url = self._redact(url), self._redact(final_url)
        clean = self._redact(dehyphenate(text))
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{label}.txt"
        path.write_text(clean, encoding="utf-8")
        host = host_of(final_url).removeprefix("www.")
        ev = TextEvidence(
            id=f"ev{n}",
            label=label,
            url=url,
            final_url=final_url,
            tier=tier_for(final_url, self._tiers),
            publisher=host,
            published=published,
            retrieved_at=datetime.now(UTC),
            blob_sha256=sha,
            text_path=str(path.relative_to(self._run_dir)),
            title=self._redact(title) if title else None,
        )
        self.items.append(ev)
        self.seen_urls.update((url, final_url))
        return ev

    # --- reading --------------------------------------------------------------------------

    def get(self, label_or_id: str) -> TextEvidence | None:
        key = label_or_id.strip()
        for ev in self.items:
            if key in (ev.label, ev.id) or key.upper() == ev.label:
                return ev
        return None

    def text(self, ev: TextEvidence) -> str:
        return (self._run_dir / ev.text_path).read_text(encoding="utf-8")

    def snippets(self, ev: TextEvidence, query: str, max_chars: int = 24_000) -> str:
        text = self.text(ev)
        if len(text) <= max_chars:
            return text
        paras = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
        qwords, qnums = _tokens(query)
        scored: list[tuple[int, int]] = []
        for i, p in enumerate(paras):
            words, nums = _tokens(p)
            scored.append((len(words & qwords) + 2 * len(nums & qnums), i))

        def render(keep: set[int], cut: bool = False) -> str:
            out: list[str] = []
            prev = -1
            for i in sorted(keep):
                if i != prev + 1:
                    out.append(GAP)
                out.append(paras[i])
                prev = i
            if prev != len(paras) - 1 or cut:  # cut: the last paragraph itself was shortened
                out.append(GAP)
            return "\n".join(out)

        keep: set[int] = set()
        used = 0
        for _score, i in sorted(scored, key=lambda s: (-s[0], s[1])):
            size = len(paras[i]) + 1
            if used + size <= max_chars:
                keep.add(i)
                used += size
        if not keep:  # every paragraph is bigger than the cap: start from the best one
            keep.add(min(scored, key=lambda s: (-s[0], s[1]))[1])
        # gap markers count against the cap: drop the lowest-scored kept paragraphs until it fits
        res = render(keep)
        while len(res) > max_chars and len(keep) > 1:
            keep.discard(min(keep, key=lambda i: (scored[i][0], -i)))
            res = render(keep)
        if len(res) > max_chars:
            (only,) = keep  # shorten the paragraph itself, reserving room for both markers
            full = paras[only]
            paras[only] = ""
            overhead = len(render(keep, cut=True))
            paras[only] = full[: max(0, max_chars - overhead)]
            res = render(keep, cut=True)
        return res[:max_chars]

    def packet(self, labels: list[str], query: str, per_source_chars: int = 24_000) -> str:
        blocks: list[str] = []
        for label in labels:
            ev = self.get(label)
            if ev is None:
                continue
            body = _TAG.sub(_defang, self.snippets(ev, query, per_source_chars))
            attrs = (
                f'id="{ev.label}" untrusted="true" url="{_attr(ev.final_url)}" '
                f'tier="{ev.tier}" published="{ev.published.isoformat() if ev.published else ""}"'
            )
            blocks.append(f"<evidence {attrs}>\n{body}\n</evidence>")
        return "\n\n".join(blocks)


def _defang(m: re.Match[str]) -> str:
    """Turn `<evidence` / `</evidence` inside fetched text into harmless `<\\evidence`."""
    return f"<\\{m[1]}evidence"


def _attr(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
