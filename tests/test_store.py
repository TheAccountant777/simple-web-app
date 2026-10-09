import os
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from kenya_data_engine.data.models import Observation, Provenance
from kenya_data_engine.data.periods import month
from kenya_data_engine.data.store import AddResult, BlobStore, SeriesStore


def _obs(value: str, blob: str = "a" * 64, label_month: int = 9) -> Observation:
    return Observation(
        series="epra.pump",
        period=month(2026, label_month),
        entity="Nairobi",
        metric="super",
        value=Decimal(value),
        unit="KES/L",
        provenance=Provenance(
            url="https://x/y.pdf",
            blob_sha256=blob,
            retrieved_at=datetime(2026, 10, 1, tzinfo=UTC),
            published=None,
            locator="p1/t0/r1/c1",
            extractor="pdfplumber@0.11.4",
        ),
    )


def test_blob_put_idempotent(tmp_path: Path) -> None:
    b = BlobStore(tmp_path / "blobs")
    sha = b.put(b"hello")
    assert b.put(b"hello") == sha
    assert b.get(sha) == b"hello"
    assert b.path(sha).exists()


def test_add_new_then_unchanged(tmp_path: Path) -> None:
    s = SeriesStore(tmp_path / "e.db")
    assert s.add([_obs("180.66")]) == AddResult(1, 0, 0)
    assert s.add([_obs("180.66")]) == AddResult(0, 1, 0)
    assert s.series_keys() == ["epra.pump"]


def test_revision_keeps_both_vintages(tmp_path: Path) -> None:
    s = SeriesStore(tmp_path / "e.db")
    s.add([_obs("180.66")])
    assert s.add([_obs("181.00")]) == AddResult(0, 0, 1)
    (latest,) = s.latest("epra.pump")
    assert latest.value == Decimal("181.00")
    assert latest.revised is True
    assert latest.vintage == 2
    hist = s.history("epra.pump", "2026-09", "Nairobi", "super")
    assert [h.value for h in hist] == [Decimal("180.66"), Decimal("181.00")]
    assert [h.revised for h in hist] == [False, True]
    assert s.latest("epra.pump", entity="Mombasa") == []


def test_decimal_roundtrip_exact(tmp_path: Path) -> None:
    s = SeriesStore(tmp_path / "e.db")
    s.add([_obs("180.66")])
    (got,) = s.latest("epra.pump")
    assert got.value == Decimal("180.66")
    assert str(got.value) == "180.66"
    assert got.period == month(2026, 9)
    assert got.provenance.locator == "p1/t0/r1/c1"


def test_latest_period_order(tmp_path: Path) -> None:
    s = SeriesStore(tmp_path / "e.db")
    s.add([_obs("2", label_month=10), _obs("1", label_month=9)])
    assert [o.period.label for o in s.latest("epra.pump")] == ["2026-09", "2026-10"]


def test_prune_keeps_referenced(tmp_path: Path) -> None:
    b = BlobStore(tmp_path / "blobs")
    keep, drop, fresh = b.put(b"keep"), b.put(b"drop"), b.put(b"fresh")
    old = time.time() - 86400 * 40
    for sha in (keep, drop):
        os.utime(b.path(sha), (old, old))
    assert len(b.prune(timedelta(days=30), {keep})) == 1
    assert b.path(keep).exists() and b.path(fresh).exists()
    assert not b.path(drop).exists()


def test_referenced_blobs(tmp_path: Path) -> None:
    s = SeriesStore(tmp_path / "e.db")
    s.add([_obs("1", blob="b" * 64)])
    assert s.referenced_blobs() == {"b" * 64}


def test_concurrent_revisions_get_distinct_vintages(tmp_path: Path) -> None:
    import threading

    db = tmp_path / "e.db"
    SeriesStore(db).add([_obs("1")])
    errors: list[BaseException] = []

    def work(v: str) -> None:
        try:
            SeriesStore(db).add([_obs(v)])
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(v,)) for v in ("2", "3")]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    hist = SeriesStore(db).history("epra.pump", "2026-09", "Nairobi", "super")
    assert [h.vintage for h in hist] == [1, 2, 3]


def test_blob_path_validates_sha(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(ValueError):
        BlobStore(tmp_path).path("../etc/passwd")


def test_put_hit_touches_mtime(tmp_path: Path) -> None:
    b = BlobStore(tmp_path / "blobs")
    sha = b.put(b"x")
    old = time.time() - 86400 * 40
    os.utime(b.path(sha), (old, old))
    assert b.put(b"x") == sha
    assert b.path(sha).stat().st_mtime > time.time() - 60
    assert b.prune(timedelta(days=30), set()) == []


def test_prune_returns_paths_and_dry_run_deletes_nothing(tmp_path: Path) -> None:
    b = BlobStore(tmp_path / "blobs")
    sha = b.put(b"old")
    old = time.time() - 86400 * 40
    os.utime(b.path(sha), (old, old))
    assert b.prune(timedelta(days=30), set(), dry_run=True) == [b.path(sha)]
    assert b.path(sha).exists()
    assert b.prune(timedelta(days=30), set()) == [b.path(sha)]
    assert not b.path(sha).exists()


def test_blob_refs_roundtrip_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "e.db"
    b = BlobStore(tmp_path / "blobs", db)
    sha = b.put(b"cited")
    assert BlobStore.referenced(db) == set()
    b.ref(sha, "evidence:r1")
    b.ref(sha, "evidence:r1")
    b.ref(sha, "evidence:r2")
    assert BlobStore.referenced(db) == {sha}
    with pytest.raises(ValueError):
        b.ref("../x", "o")
    with pytest.raises(ValueError, match="db_path"):
        BlobStore(tmp_path / "blobs").ref(sha, "o")
