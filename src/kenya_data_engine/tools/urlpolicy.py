"""URL safety policy (SSRF guard) and content sniffing."""

import asyncio
import ipaddress
import socket
import zipfile
from collections.abc import Awaitable, Callable
from io import BytesIO
from typing import Literal
from urllib.parse import urlsplit

import httpx

from kenya_data_engine.errors import FetchError


class UnsafeUrl(FetchError):
    """The URL (or a redirect hop) points somewhere we refuse to fetch."""


Resolver = Callable[[str], Awaitable[list[str]]]
Sniffed = Literal["pdf", "xlsx", "xls", "html", "csv", "json", "unknown"]


async def _system_resolve(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def _is_global(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


async def check_url(url: str, resolve: Resolver | None = None) -> None:
    """Raise UnsafeUrl unless `url` is http(s), has no userinfo and resolves only to global IPs."""
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 - validates the port
        target = httpx.URL(url)  # the parser the request will use; the two must agree
    except (ValueError, httpx.InvalidURL) as exc:
        raise UnsafeUrl(f"malformed URL: {exc}") from exc
    if parts.scheme not in ("http", "https"):
        raise UnsafeUrl(f"unsupported URL scheme {parts.scheme!r}: {url}")
    if parts.username is not None or parts.password is not None or target.userinfo:
        raise UnsafeUrl(f"URL contains credentials: {parts.scheme}://{target.host}/...")
    host = target.host
    if host != (parts.hostname or "").lower():
        raise UnsafeUrl(f"URL host is ambiguous: {url}")
    if not host:
        raise UnsafeUrl(f"URL has no host: {url}")
    try:
        addrs = [str(ipaddress.ip_address(host))]  # literal IP: never trust the resolver
    except ValueError:
        addrs = []
    try:
        addrs = addrs or await (resolve or _system_resolve)(host)
    except (OSError, UnicodeError) as exc:
        raise UnsafeUrl(f"cannot resolve {host}: {exc}") from exc
    if not addrs:
        raise UnsafeUrl(f"cannot resolve {host}")
    for addr in addrs:
        check_addr(host, addr)


def check_addr(host: str, addr: str) -> None:
    """Raise UnsafeUrl unless `addr` is a global IP. Used on resolved and on connected addresses."""
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])
    except ValueError as exc:
        raise UnsafeUrl(f"{host} resolved to a non-IP address {addr!r}") from exc
    if not _is_global(ip):
        raise UnsafeUrl(f"{host} resolves to non-public address {ip}")


def sniff(content: bytes) -> Sniffed:
    """Identify content by magic bytes, ignoring any declared content type."""
    if content.startswith(b"%PDF-"):
        return "pdf"
    if content.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(BytesIO(content)) as zf:
                if any(n.startswith("xl/") for n in zf.namelist()):
                    return "xlsx"
        except zipfile.BadZipFile:
            pass
        return "unknown"
    if content.startswith(bytes.fromhex("D0CF11E0")):
        return "xls"
    head = content[:4096]
    try:
        text = head.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        if exc.start < len(head) - 4:  # a cut multibyte tail is fine; real garbage is not
            return "unknown"
        text = head[: exc.start].decode("utf-8-sig")
    stripped = text.lstrip()
    if stripped.startswith("<"):
        return "html"
    if stripped.startswith(("{", "[")):
        return "json"
    if "\x00" in text or not stripped:
        return "unknown"
    return "csv"
