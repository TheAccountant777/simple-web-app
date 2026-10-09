"""AIA chasing for servers that omit their intermediate certificate. No real network."""

import hashlib
import ssl
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import tenacity
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import AuthorityInformationAccessOID, NameOID

from kenya_data_engine import tls
from kenya_data_engine.cache import Cache
from kenya_data_engine.errors import FetchError
from kenya_data_engine.http import fetch
from kenya_data_engine.tls import AiaFixer, aia_issuer_urls, is_incomplete_chain

CHAIN_ERR = (
    "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
    "unable to get local issuer certificate (_ssl.c:1006)"
)
AIA_URL = "http://ca.example.ke/intermediate.crt"


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr("kenya_data_engine.http._WAIT", tenacity.wait_none())


_REAL_TRUSTED_ROOTS = tls._trusted_roots


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def make_cert(cn, issuer_cn, key, signer, *, ca: bool, aia: str | None = None):
    now = datetime.now(UTC)
    b = (
        x509.CertificateBuilder()
        .subject_name(_name(cn))
        .issuer_name(_name(issuer_cn))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    )
    if aia:
        b = b.add_extension(
            x509.AuthorityInformationAccess(
                [
                    x509.AccessDescription(
                        AuthorityInformationAccessOID.OCSP,
                        x509.UniformResourceIdentifier("http://ocsp.example.ke"),
                    ),
                    x509.AccessDescription(
                        AuthorityInformationAccessOID.CA_ISSUERS,
                        x509.UniformResourceIdentifier(aia),
                    ),
                ]
            ),
            critical=False,
        )
    return b.sign(signer, hashes.SHA256())


@pytest.fixture
def chain():
    root_key, mid_key, leaf_key = (ec.generate_private_key(ec.SECP256R1()) for _ in range(3))
    root = make_cert("Root", "Root", root_key, root_key, ca=True)
    mid = make_cert("Mid", "Root", mid_key, root_key, ca=True)
    leaf = make_cert("site.ke", "Mid", leaf_key, mid_key, ca=False, aia=AIA_URL)
    return root, mid, leaf


@pytest.fixture(autouse=True)
def trust_test_root(chain, monkeypatch):
    monkeypatch.setattr(tls, "_trusted_roots", lambda: (chain[0],))


def der(cert) -> bytes:
    return cert.public_bytes(serialization.Encoding.DER)


def pem(cert) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def test_aia_issuer_urls_reads_ca_issuers_only(chain):
    _, mid, leaf = chain
    assert aia_issuer_urls(leaf) == [AIA_URL]
    assert aia_issuer_urls(mid) == []  # no AIA extension at all


@pytest.mark.parametrize(
    "msg,expected",
    [
        (CHAIN_ERR, True),
        ("[SSL: CERTIFICATE_VERIFY_FAILED] certificate has expired", False),
        ("connection refused", False),
    ],
)
def test_is_incomplete_chain(msg, expected):
    assert is_incomplete_chain(httpx.ConnectError(msg)) is expected
    assert is_incomplete_chain(httpx.ReadError(CHAIN_ERR)) is False  # only connect errors


@pytest.mark.parametrize("encode", [der, pem], ids=["der", "pem"])
async def test_fixer_downloads_converts_and_caches_the_intermediate(
    tmp_path, chain, monkeypatch, encode
):
    _, mid, leaf = chain
    seen = {}

    def fake_leaf(host, port):
        seen["peer"] = (host, port)
        return der(leaf)

    async def fake_download(url):
        seen["url"] = url
        return encode(mid)

    monkeypatch.setattr(tls, "_fetch_leaf_der", fake_leaf)
    monkeypatch.setattr(tls, "_download", fake_download)
    fixer = AiaFixer(tmp_path / "certs")
    client = await fixer.client_for("https://www.site.ke/page")
    assert seen == {"peer": ("www.site.ke", 443), "url": AIA_URL}
    files = list((tmp_path / "certs").glob("*.pem"))
    assert [f.name for f in files] == [hashlib.sha256(der(mid)).hexdigest() + ".pem"]
    assert x509.load_pem_x509_certificate(files[0].read_bytes()) == mid
    assert await fixer.client_for("https://www.site.ke/other") is client  # one reused client
    await fixer.aclose()


async def test_fixer_loads_all_cached_intermediates_into_the_context(tmp_path, chain, monkeypatch):
    root, mid, leaf = chain
    certs = tmp_path / "certs"
    certs.mkdir()
    (certs / "old.pem").write_bytes(pem(root))
    monkeypatch.setattr(tls, "_fetch_leaf_der", lambda h, p: der(leaf))

    async def fake_download(url):
        return der(mid)

    monkeypatch.setattr(tls, "_download", fake_download)
    fixer = AiaFixer(certs)
    await fixer.client_for("https://site.ke/")
    names = {c.get("subject") for c in fixer.ssl_context().get_ca_certs()}
    subjects = {tuple(x[0][1] for x in n) for n in names}
    assert ("Root",) in subjects and ("Mid",) in subjects
    await fixer.aclose()


async def test_fixer_fails_without_an_aia_url(tmp_path, chain, monkeypatch):
    _, mid, _ = chain
    monkeypatch.setattr(tls, "_fetch_leaf_der", lambda h, p: der(mid))
    with pytest.raises(ValueError, match="CA Issuers"):
        await AiaFixer(tmp_path / "c").client_for("https://site.ke/")


async def test_fetch_retries_once_with_augmented_client(respx_mock, tmp_path):
    route = respx_mock.get("https://site.ke/x").mock(
        side_effect=[httpx.ConnectError(CHAIN_ERR), httpx.Response(200, text="secured")]
    )
    called = []

    class Fixer:
        async def client_for(self, url):
            called.append(url)
            return httpx.AsyncClient()

    async with httpx.AsyncClient() as c:
        r = await fetch(
            "https://site.ke/x", client=c, cache=Cache(tmp_path / "e.db"), ttl_hours=1, aia=Fixer()
        )
    assert r.content == b"secured" and route.call_count == 2 and called == ["https://site.ke/x"]


@pytest.mark.parametrize("fixer_error", [ValueError("no CA Issuers"), OSError("net down")])
async def test_fetch_raises_original_error_with_hint_when_repair_fails(
    respx_mock, tmp_path, fixer_error
):
    route = respx_mock.get("https://site.ke/x").mock(side_effect=httpx.ConnectError(CHAIN_ERR))

    class Fixer:
        async def client_for(self, url):
            raise fixer_error

    async with httpx.AsyncClient() as c:
        with pytest.raises(FetchError) as ei:
            await fetch(
                "https://site.ke/x",
                client=c,
                cache=Cache(tmp_path / "e.db"),
                ttl_hours=1,
                aia=Fixer(),
            )
    assert "CERTIFICATE_VERIFY_FAILED" in ei.value.message
    assert "server sends an incomplete certificate chain" in (ei.value.hint or "")
    assert route.call_count == 1  # no blind retries of a certificate error


async def test_chain_error_without_fixer_still_gets_the_hint(respx_mock, tmp_path):
    respx_mock.get("https://site.ke/x").mock(side_effect=httpx.ConnectError(CHAIN_ERR))
    async with httpx.AsyncClient() as c:
        with pytest.raises(FetchError) as ei:
            await fetch("https://site.ke/x", client=c, cache=Cache(tmp_path / "e.db"), ttl_hours=1)
    assert "incomplete certificate chain" in (ei.value.hint or "")


async def test_other_connect_errors_do_not_trigger_the_fixer(respx_mock, tmp_path):
    respx_mock.get("https://site.ke/x").mock(side_effect=httpx.ConnectError("refused"))

    class Fixer:
        async def client_for(self, url):
            raise AssertionError("must not be called")

    async with httpx.AsyncClient() as c:
        with pytest.raises(FetchError):
            await fetch(
                "https://site.ke/x",
                client=c,
                cache=Cache(tmp_path / "e.db"),
                ttl_hours=1,
                aia=Fixer(),
            )


async def test_adapters_pass_the_context_fixer(respx_mock, ctx):
    from kenya_data_engine.radar.rss import RssAdapter

    respx_mock.get("https://site.ke/feed").mock(
        side_effect=[
            httpx.ConnectError(CHAIN_ERR),
            httpx.Response(200, content=b"<rss version='2.0'><channel></channel></rss>"),
        ]
    )
    used = []

    async def client_for(url):
        used.append(url)
        return httpx.AsyncClient()

    ctx.tls.client_for = client_for
    assert (
        await RssAdapter("x", "https://site.ke/feed").fetch(ctx, datetime(2000, 1, 1, tzinfo=UTC))
        == []
    )
    assert used == ["https://site.ke/feed"]


def test_validate_accepts_intermediate_issued_by_trusted_root(chain):
    tls.validate_intermediate(chain[1])


def test_validate_rejects_self_signed_ca():
    k = ec.generate_private_key(ec.SECP256R1())
    rogue = make_cert("Rogue Root", "Rogue Root", k, k, ca=True)
    with pytest.raises(ValueError, match="self-signed"):
        tls.validate_intermediate(rogue)


def test_validate_rejects_non_ca(chain):
    with pytest.raises(ValueError, match="not a CA"):
        tls.validate_intermediate(chain[2])


def test_validate_rejects_ca_from_untrusted_issuer():
    rk, mk = (ec.generate_private_key(ec.SECP256R1()) for _ in range(2))
    evil = make_cert("Evil Mid", "Evil Root", mk, rk, ca=True)
    with pytest.raises(ValueError, match="trusted root"):
        tls.validate_intermediate(evil)


def test_validate_rejects_forged_issuer_name(chain):
    """Claims the trusted root's name but is signed by another key."""
    fk, mk = (ec.generate_private_key(ec.SECP256R1()) for _ in range(2))
    forged = make_cert("Mid2", "Root", mk, fk, ca=True)
    with pytest.raises(ValueError, match="trusted root"):
        tls.validate_intermediate(forged)


async def test_fixer_refuses_to_cache_rogue_ca(tmp_path, chain, monkeypatch):
    k = ec.generate_private_key(ec.SECP256R1())
    rogue = make_cert("Rogue Root", "Rogue Root", k, k, ca=True)
    monkeypatch.setattr(tls, "_fetch_leaf_der", lambda h, p: der(chain[2]))

    async def fake_download(url):
        return der(rogue)

    monkeypatch.setattr(tls, "_download", fake_download)
    fixer = AiaFixer(tmp_path / "certs")
    with pytest.raises(Exception):  # noqa: B017 - any refusal is fine; nothing may be cached
        await fixer.client_for("https://site.ke/")
    assert not list((tmp_path / "certs").glob("*.pem"))
    await fixer.aclose()


def test_context_disables_partial_chains(tmp_path):
    ctx = AiaFixer(tmp_path / "certs").ssl_context()
    assert not ctx.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN


def _keys(n):
    return [ec.generate_private_key(ec.SECP256R1()) for _ in range(n)]


async def test_fixer_follows_two_hop_chain_to_trusted_root(tmp_path, monkeypatch):
    rk, k1, k2, lk = _keys(4)
    root = make_cert("Root2", "Root2", rk, rk, ca=True)
    monkeypatch.setattr(tls, "_trusted_roots", lambda: (root,))
    cross = make_cert("Cross", "Root2", k1, rk, ca=True)
    issuing = make_cert("Issuing", "Cross", k2, k1, ca=True, aia="http://aia.example/cross.crt")
    leaf = make_cert("site.ke", "Issuing", lk, k2, ca=False, aia="http://aia.example/issuing.crt")
    served = {"http://aia.example/issuing.crt": issuing, "http://aia.example/cross.crt": cross}
    monkeypatch.setattr(tls, "_fetch_leaf_der", lambda h, p: der(leaf))

    async def fake_download(url):
        return der(served[url])

    monkeypatch.setattr(tls, "_download", fake_download)
    fixer = AiaFixer(tmp_path / "certs")
    await fixer.client_for("https://site.ke/")
    stored = {
        x509.load_pem_x509_certificate(f.read_bytes()) for f in (tmp_path / "certs").glob("*.pem")
    }
    assert stored == {issuing, cross}
    await fixer.aclose()


async def test_fixer_rejects_link_not_signed_by_downloaded_cert(tmp_path, monkeypatch):
    rk, mk, ok, lk = _keys(4)
    root = make_cert("R", "R", rk, rk, ca=True)
    monkeypatch.setattr(tls, "_trusted_roots", lambda: (root,))
    mid = make_cert("Mid", "R", mk, rk, ca=True)
    # leaf names "Mid" as issuer but is signed by another key
    leaf = make_cert("site.ke", "Mid", lk, ok, ca=False, aia="http://aia.example/mid.crt")
    monkeypatch.setattr(tls, "_fetch_leaf_der", lambda h, p: der(leaf))

    async def fake_download(url):
        return der(mid)

    monkeypatch.setattr(tls, "_download", fake_download)
    fixer = AiaFixer(tmp_path / "certs")
    with pytest.raises(ValueError, match="did not sign"):
        await fixer.client_for("https://site.ke/")
    assert not list((tmp_path / "certs").glob("*.pem"))
    await fixer.aclose()


async def test_fixer_gives_up_after_max_hops(tmp_path, monkeypatch):
    keys = _keys(6)
    monkeypatch.setattr(tls, "_trusted_roots", lambda: ())
    certs = {}
    # c4 <- c3 <- c2 <- c1 <- leaf, none reaching a trusted root
    for i in range(4, 0, -1):
        certs[i] = make_cert(f"C{i}", f"C{i + 1}", keys[i], keys[i + 1], ca=True,
                             aia=f"http://aia.example/c{i + 1}.crt")  # fmt: skip
    leaf = make_cert("site.ke", "C1", keys[0], keys[1], ca=False, aia="http://aia.example/c1.crt")
    monkeypatch.setattr(tls, "_fetch_leaf_der", lambda h, p: der(leaf))

    async def fake_download(url):
        return der(certs[int(url.removeprefix("http://aia.example/c").removesuffix(".crt"))])

    monkeypatch.setattr(tls, "_download", fake_download)
    fixer = AiaFixer(tmp_path / "certs")
    with pytest.raises(ValueError, match="within 3 hops"):
        await fixer.client_for("https://site.ke/")
    assert not list((tmp_path / "certs").glob("*.pem"))
    await fixer.aclose()


def test_trusted_roots_load_without_warnings(recwarn):
    _REAL_TRUSTED_ROOTS.cache_clear()
    _REAL_TRUSTED_ROOTS()
    assert not [w for w in recwarn if "serial number" in str(w.message)]
