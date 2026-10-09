"""Synthesize minimal valid RSS and listing HTML for any configured source (offline)."""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import format_datetime
from html import escape

from kenya_data_engine.config import SourceSpec

N_ITEMS = 3
WORDS = ("alpha", "beta", "gamma")  # letters only: a date cell may also contain the title
_STEP = re.compile(
    r"^(?P<tag>[a-z][a-z0-9]*)?(?:#(?P<id>[\w-]+))?(?P<cls>(?:\.[\w-]+)*)"
    r"(?::(?:first-child|nth-child\((?P<nth>\d+)\)))?$"
)


@dataclass
class Node:
    tag: str
    id: str | None = None
    classes: tuple[str, ...] = ()
    nth: int | None = None
    text: str = ""
    href: str = ""
    children: list["Node"] = field(default_factory=list)

    def anchors(self) -> list["Node"]:
        found = [self] if self.tag == "a" else []
        for child in self.children:
            found.extend(child.anchors())
        return found

    def render(self) -> str:
        attrs = (f' id="{self.id}"' if self.id else "") + (
            f' class="{" ".join(self.classes)}"' if self.classes else ""
        )
        if self.tag == "a":
            attrs += f' href="{self.href}"'
        inner = escape(self.text) + "".join(c.render() for c in self.children)
        return f"<{self.tag}{attrs}>{inner}</{self.tag}>"


def _step(token: str) -> Node:
    m = _STEP.match(token)
    if m is None:
        raise ValueError(f"sourcegen cannot handle selector step {token!r}")
    nth = int(m["nth"]) if m["nth"] else (1 if ":first-child" in token else None)
    classes = tuple(c for c in m["cls"].split(".") if c)
    return Node(m["tag"] or "span", m["id"], classes, nth)


def _matches(node: Node, want: Node) -> bool:
    return node.tag == want.tag and node.id == want.id and set(want.classes) <= set(node.classes)


def ensure_path(parent: Node, chain: str) -> Node:
    """Create (or reuse) the descendants of `parent` that make `chain` match."""
    node = parent
    for token in chain.split():
        want = _step(token)
        if want.nth:
            while len(node.children) < want.nth - 1:  # filler siblings before the nth child
                node.children.append(Node(want.tag))
            if len(node.children) >= want.nth and _matches(node.children[want.nth - 1], want):
                node = node.children[want.nth - 1]
                continue
            if len(node.children) >= want.nth:  # filler there already: adopt it
                filler = node.children[want.nth - 1]
                filler.id, filler.classes = want.id, want.classes
                node = filler
                continue
            node.children.append(want)
            node = want
            continue
        found = next((c for c in node.children if _matches(c, want)), None)
        if found is None:
            found = want
            node.children.append(found)
        node = found
    return node


def listing_html(name: str, spec: SourceSpec) -> str:
    """HTML that the spec's selectors extract N_ITEMS items from (first union branch)."""
    first = lambda sel: sel.split(",")[0].strip()  # noqa: E731
    body = Node("body")
    item_tokens = first(spec.item or "").split()
    holder = ensure_path(body, " ".join(item_tokens[:-1])) if len(item_tokens) > 1 else body
    today = datetime.now(UTC).strftime("%B %d, %Y")
    for i in range(N_ITEMS):
        item = _step(item_tokens[-1])
        holder.children.append(item)
        ensure_path(item, first(spec.title or "")).text = f"{name} headline {WORDS[i]}"
        ensure_path(item, first(spec.link or ""))
        if spec.date:
            ensure_path(item, first(spec.date)).text = today
        for a in item.anchors():
            a.href = f"/{name}/{i}"
    return f"<html><body>{''.join(c.render() for c in body.children)}</body></html>"


def rss_xml(name: str) -> str:
    now = datetime.now(UTC)
    items = "".join(
        f"<item><title>{name} headline {i}</title><link>https://{name}.example.ke/{i}</link>"
        f"<pubDate>{format_datetime(now)}</pubDate><description>about {i}</description></item>"
        for i in range(N_ITEMS)
    )
    return f'<rss version="2.0"><channel><title>{name}</title>{items}</channel></rss>'
