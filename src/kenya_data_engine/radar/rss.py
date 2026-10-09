"""RSS/Atom adapter (news feeds and Google Trends)."""

from datetime import UTC, datetime
from typing import Any

import feedparser  # type: ignore[import-untyped]
from selectolax.lexbor import LexborHTMLParser

from kenya_data_engine.context import RunContext
from kenya_data_engine.http import fetch
from kenya_data_engine.models import Signal, SignalKind, signal_id

SNIPPET_CHARS = 300


def strip_html(text: str) -> str:
    if not text.strip():
        return ""
    tree = LexborHTMLParser(text)
    return " ".join((tree.text(separator=" ") or "").split())


def _entry_date(entry: Any) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    y, mo, d, h, mi, sec = parsed[:6]
    return datetime(y, mo, d, h, mi, sec, tzinfo=UTC)


class RssAdapter:
    def __init__(
        self, name: str, url: str, kind: SignalKind = "news", user_agent: str | None = None
    ) -> None:
        self.name = name
        self.url = url
        self.user_agent = user_agent
        self.kind: SignalKind = kind

    async def fetch(self, ctx: RunContext, since: datetime) -> list[Signal]:
        res = await fetch(
            self.url,
            client=ctx.http,
            cache=ctx.cache,
            ttl_hours=ctx.config.cache_ttl_hours,
            headers={"User-Agent": self.user_agent} if self.user_agent else None,
            aia=ctx.tls,
        )
        feed = feedparser.parse(res.content)
        signals: list[Signal] = []
        for entry in feed.entries:
            published = _entry_date(entry)
            title = " ".join(str(entry.get("title", "")).split())
            if published is None or published < since or not title:
                continue
            url = entry.get("link") or None
            signals.append(
                Signal(
                    id=signal_id(url, title),
                    kind=self.kind,
                    title=title,
                    source=self.name,
                    url=url,
                    published_at=published,
                    snippet=strip_html(str(entry.get("summary", "")))[:SNIPPET_CHARS],
                )
            )
        return signals
