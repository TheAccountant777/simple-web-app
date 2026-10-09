"""Repair for servers that omit their intermediate certificate (AIA chasing).

Some Kenyan government sites send only their leaf certificate. Browsers quietly fetch the
missing intermediate from the leaf's Authority Information Access URL; Python does not, so
verification fails with "unable to get local issuer certificate". `AiaFixer` does what a
browser does: it downloads the intermediate, caches it, and hands back a client whose trust
store is certifi plus those intermediates. Verification stays ON: the leaf must still chain
to a trusted root.
"""

import asyncio
import functools
import hashlib
import socket
import ssl
import warnings
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import certifi
import httpx
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.utils import CryptographyDeprecationWarning
from cryptography.x509.oid import AuthorityInformationAccessOID

MAX_CERT_BYTES = 256 * 1024
_NETWORK_TIMEOUT_S = 10.0


def is_incomplete_chain(exc: Exception) -> bool:
    text = str(exc)
    return (
        isinstance(exc, httpx.ConnectError)
        and "CERTIFICATE_VERIFY_FAILED" in text
        and "unable to get local issuer" in text
    )


@functools.cache
def _trusted_roots() -> tuple[x509.Certificate, ...]:
    # certifi still ships an old root with a non-positive serial; cryptography warns about it.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", CryptographyDeprecationWarning)
        return tuple(x509.load_pem_x509_certificates(Path(certifi.where()).read_bytes()))


MAX_HOPS = 3


def _check_ca(cert: x509.Certificate) -> None:
    try:
        bc = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
    except x509.ExtensionNotFound:
        bc = None
    if bc is None or not bc.ca:
        raise ValueError("downloaded certificate is not a CA certificate")
    if cert.issuer == cert.subject:
        raise ValueError("downloaded certificate is self-signed; refusing to trust it")
    now = datetime.now(UTC)
    if not (cert.not_valid_before_utc <= now <= cert.not_valid_after_utc):
        raise ValueError("downloaded certificate is not currently valid")


def _signed_by(cert: x509.Certificate, issuer: x509.Certificate) -> bool:
    if cert.issuer != issuer.subject:
        return False
    try:
        cert.verify_directly_issued_by(issuer)
    except (ValueError, TypeError, InvalidSignature):
        return False
    return True


def _issued_by_trusted_root(cert: x509.Certificate) -> bool:
    return any(_signed_by(cert, root) for root in _trusted_roots())


def validate_intermediate(cert: x509.Certificate) -> None:
    """Refuse anything but a current CA certificate directly issued by a trusted root.

    The issuer URL comes from a certificate read over an unverified connection, so whatever
    it downloads is untrusted input. Without this check an attacker could hand us their own
    CA and, once cached in the trust store, it would vouch for any site.
    """
    _check_ca(cert)
    if not _issued_by_trusted_root(cert):
        raise ValueError("downloaded certificate is not issued by a trusted root")


def aia_issuer_urls(cert: x509.Certificate) -> list[str]:
    """The "CA Issuers" URLs from a certificate's Authority Information Access extension."""
    try:
        ext = cert.extensions.get_extension_for_class(x509.AuthorityInformationAccess)
    except x509.ExtensionNotFound:
        return []
    return [
        d.access_location.value
        for d in ext.value
        if d.access_method == AuthorityInformationAccessOID.CA_ISSUERS
        and isinstance(d.access_location, x509.UniformResourceIdentifier)
    ]


def _fetch_leaf_der(host: str, port: int) -> bytes:
    """Read the server's leaf certificate. Blocking.

    This connection is deliberately unverified and carries no request: it only reads the
    certificate so we can find its issuer URL. Nothing is sent over it, and nothing fetched
    through it is trusted; the real request is verified against the trust store.
    """
    probe = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    probe.check_hostname = False
    probe.verify_mode = ssl.CERT_NONE
    with (
        socket.create_connection((host, port), timeout=_NETWORK_TIMEOUT_S) as sock,
        probe.wrap_socket(sock, server_hostname=host) as tls,
    ):
        leaf = tls.getpeercert(binary_form=True)
    if not leaf:
        raise ValueError("the server presented no certificate")
    return leaf


async def _download(url: str) -> bytes:
    if urlsplit(url).scheme not in ("http", "https"):
        raise ValueError(f"unsupported certificate URL {url}")
    async with httpx.AsyncClient(timeout=_NETWORK_TIMEOUT_S, follow_redirects=True) as client:
        resp = await client.get(url)
    resp.raise_for_status()
    if len(resp.content) > MAX_CERT_BYTES:
        raise ValueError("issuer certificate is unreasonably large")
    return resp.content


def _parse_cert(data: bytes) -> x509.Certificate:
    try:
        return x509.load_pem_x509_certificate(data)
    except ValueError:
        return x509.load_der_x509_certificate(data)


class AiaFixer:
    """Builds (and reuses) an httpx client that trusts certifi plus cached intermediates."""

    def __init__(self, certs_dir: Path) -> None:
        self.certs_dir = certs_dir
        self._client: httpx.AsyncClient | None = None
        self._loaded: tuple[str, ...] = ()

    def ssl_context(self) -> ssl.SSLContext:
        context = ssl.create_default_context(cafile=certifi.where())
        # Cached intermediates must never act as trust anchors: every chain has to reach a
        # self-signed root from certifi. (Python 3.13+ enables partial chains by default.)
        context.verify_flags &= ~ssl.VERIFY_X509_PARTIAL_CHAIN
        for pem in sorted(self.certs_dir.glob("*.pem")):
            context.load_verify_locations(cafile=str(pem))
        return context

    def _store(self, cert: x509.Certificate) -> None:
        der = cert.public_bytes(serialization.Encoding.DER)
        self.certs_dir.mkdir(parents=True, exist_ok=True)
        path = self.certs_dir / f"{hashlib.sha256(der).hexdigest()}.pem"
        path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    async def _chain_to_root(self, leaf: x509.Certificate) -> list[x509.Certificate]:
        """Follow CA Issuers URLs from the leaf until a certificate issued by a trusted root.

        Every link is checked: each downloaded certificate must be a current, non-self-signed
        CA that actually signed the certificate below it, and the top one must be signed by a
        certifi root. Nothing is cached unless the whole chain checks out.
        """
        chain: list[x509.Certificate] = []
        current = leaf
        for _ in range(MAX_HOPS):
            urls = aia_issuer_urls(current)
            if not urls:
                raise ValueError(
                    f"no CA Issuers URL on {current.subject.rfc4514_string()} to fetch from"
                )
            last: Exception | None = None
            for issuer_url in urls:
                try:
                    issuer = _parse_cert(await _download(issuer_url))
                    _check_ca(issuer)
                    if not _signed_by(current, issuer):
                        raise ValueError("downloaded certificate did not sign the one below it")
                    break
                except Exception as exc:
                    last = exc
            else:
                raise ValueError(f"could not download the intermediate certificate ({last})")
            chain.append(issuer)
            if _issued_by_trusted_root(issuer):
                return chain
            current = issuer
        raise ValueError(
            f"chain does not reach a trusted root within {MAX_HOPS} hops "
            f"(last issuer: {current.issuer.rfc4514_string()})"
        )

    async def client_for(self, url: str) -> httpx.AsyncClient:
        """Fetch and cache the missing intermediate for `url`'s server; return a client."""
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = parts.port or (80 if parts.scheme == "http" else 443)
        leaf = x509.load_der_x509_certificate(await asyncio.to_thread(_fetch_leaf_der, host, port))
        for cert in await self._chain_to_root(leaf):
            self._store(cert)
        loaded = tuple(sorted(p.name for p in self.certs_dir.glob("*.pem")))
        if self._client is None or loaded != self._loaded:
            if self._client is not None:
                await self._client.aclose()
            self._client = httpx.AsyncClient(verify=self.ssl_context())
            self._loaded = loaded
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
