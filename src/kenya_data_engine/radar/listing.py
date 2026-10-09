"""CSS-selector adapter for listing pages (CBK, KNBS, EPRA, Parliament)."""

from datetime import UTC, datetime
from urllib.parse import urljoin

from dateutil import parser as dateparser
from selectolax.lexbor import LexborHTMLParser

from kenya_data_engine.config import ListingSpec
from kenya_data_engine.context import RunContext
from kenya_data_engine.errors import FetchError
from kenya_data_engine.http import fetch
from kenya_data_engine.models import Signal, signal_id


def _parse_date(text: str) -> datetime | None:
    try:
        parsed = dateparser.parse(text, fuzzy=True)
    except (ValueError, OverflowError):
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


class ListingAdapter:
    def __init__(self, name: str, spec: ListingSpec, max_items: int) -> None:
        self.name = name
        self.spec = spec
        self.max_items = max_items

    async def fetch(self, ctx: RunContext, since: datetime) -> list[Signal]:
        spec = self.spec
        res = await fetch(
            spec.url, client=ctx.http, cache=ctx.cache, ttl_hours=ctx.config.cache_ttl_hours
        )
        tree = LexborHTMLParser(res.content.decode("utf-8", errors="replace"))
        items = tree.css(spec.item)
        if not items:
            raise FetchError(
                f"{self.name}: selector matched nothing — page layout may have changed",
                hint="run engine doctor",
            )
        signals: list[Signal] = []
        for item in items:
            title_node = item.css_first(spec.title)
            title = " ".join(title_node.text().split()) if title_node else ""
            if not title:
                continue
            link_node = item.css_first(spec.link)
            href = link_node.attributes.get("href") if link_node else None
            url = urljoin(spec.url, href) if href else None
            date_node = item.css_first(spec.date) if spec.date else None
            published = _parse_date(date_node.text()) if date_node else None
            if published is not None and published < since:
                continue
            signals.append(
                Signal(
                    id=signal_id(url, title),
                    kind=spec.kind,
                    title=title,
                    source=self.name,
                    url=url,
                    published_at=published,
                )
            )
            if len(signals) >= self.max_items:
                break
        return signals
