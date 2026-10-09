"""Throwaway probe for Spike 3: run on a laptop with open internet access.

Usage: uv run python spikes/sources.py [--save]

Reads the feed and listing URLs/selectors from the packaged defaults, prints status,
entry counts and sample titles for each, and with --save writes the raw responses to
tests/fixtures/rss/ and tests/fixtures/listing/ (overwriting the hand-built fixtures).
"""

import sys
from importlib import resources
from pathlib import Path

import feedparser
import httpx
import yaml
from selectolax.lexbor import LexborHTMLParser

ROOT = Path(__file__).resolve().parent.parent
UA = "kenya-data-engine-spike/0.1 (+research)"


def main() -> None:
    save = "--save" in sys.argv
    cfg = yaml.safe_load(
        resources.files("kenya_data_engine").joinpath("defaults/config.yaml").read_text("utf-8")
    )["radar"]
    feeds = {**cfg["feeds"], "google_trends": cfg["trends_feed"]}
    with httpx.Client(headers={"User-Agent": UA}, follow_redirects=True, timeout=20) as client:
        for name, url in feeds.items():
            try:
                r = client.get(url)
                parsed = feedparser.parse(r.content)
                dated = sum(1 for e in parsed.entries if e.get("published_parsed"))
                print(f"[rss] {name}: HTTP {r.status_code}, {len(parsed.entries)} entries, "
                      f"{dated} dated, {url}")
                if save and r.is_success:
                    (ROOT / f"tests/fixtures/rss/{name}.xml").write_bytes(r.content)
            except httpx.HTTPError as exc:
                print(f"[rss] {name}: ERROR {exc}")
        for name, spec in cfg["listings"].items():
            try:
                r = client.get(spec["url"])
                tree = LexborHTMLParser(r.text)
                items = tree.css(spec["item"])
                print(f"[listing] {name}: HTTP {r.status_code}, {len(items)} items "
                      f"for {spec['item']!r}, {spec['url']}")
                for it in items[:3]:
                    t = it.css_first(spec["title"])
                    d = it.css_first(spec["date"]) if spec.get("date") else None
                    link = it.css_first(spec["link"])
                    print("   ", t.text(strip=True) if t else None,
                          "|", link.attributes.get("href") if link else None,
                          "|", d.text(strip=True) if d else None)
                if save and r.is_success:
                    (ROOT / f"tests/fixtures/listing/{name}.html").write_text(r.text, "utf-8")
            except httpx.HTTPError as exc:
                print(f"[listing] {name}: ERROR {exc}")


if __name__ == "__main__":
    main()
