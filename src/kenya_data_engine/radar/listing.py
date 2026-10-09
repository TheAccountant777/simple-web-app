"""CSS-selector adapter for listing pages (regulators, Parliament, news section pages)."""

from datetime import UTC, datetime
from urllib.parse import urljoin

from dateutil import parser as dateparser
from selectolax.lexbor import LexborHTMLParser

from kenya_data_engine.config import SourceSpec
from kenya_data_engine.context import RunContext
from kenya_data_engine.errors import ConfigError, FetchError
from kenya_data_engine.http import fetch
from kenya_data_engine.models import Signal, signal_id


def _parse_date(text: str) -> datetime | None:
    try:
        parsed = dateparser.parse(text, fuzzy=True)
    except (ValueError, OverflowError):
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


class ListingAdapter:
    def __init__(self, name: str, spec: SourceSpec, max_items: int) -> None:
        if not (spec.item and spec.title and spec.link):
            raise ConfigError(f"{name}: a listing source needs item, title and link selectors")
        self.name = name
        self.spec = spec
        self.user_agent = spec.user_agent
        self.item, self.title_sel, self.link_sel = spec.item, spec.title, spec.link
        self.max_items = max_items

    async def fetch(self, ctx: RunContext, since: datetime) -> list[Signal]:
        spec = self.spec
        res = await fetch(
            spec.url,
            client=ctx.http,
            cache=ctx.cache,
            ttl_hours=ctx.config.cache_ttl_hours,
            headers={"User-Agent": spec.user_agent} if spec.user_agent else None,
            aia=ctx.tls,
        )
        tree = LexborHTMLParser(res.content.decode("utf-8", errors="replace"))
        # A union like `a tr, tr` returns a node once per branch that matches it.
        items = list({n.mem_id: n for n in tree.css(self.item)}.values())
        if not items:
            raise FetchError(
                f"{self.name}: selector matched nothing — page layout may have changed",
                hint=f"run `engine sources test {self.name}`",
            )
        signals: list[Signal] = []
        for item in items:
            title_node = item.css_first(self.title_sel)
            title = " ".join(title_node.text().split()) if title_node else ""
            href = next(
                (h for n in item.css(self.link_sel) if (h := n.attributes.get("href"))), None
            )
            if not title or not href:
                continue  # an item we cannot use is skipped, not an error
            url = urljoin(spec.url, href)
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
