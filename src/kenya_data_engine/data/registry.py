"""The series registry: catalog.yaml entries and `fetch_series`, the whole fetch pipeline."""

from datetime import date
from importlib import resources
from typing import Any, Literal

from pydantic import Field, ValidationError

from kenya_data_engine.config import _load_yaml, _Strict
from kenya_data_engine.context import RunContext
from kenya_data_engine.data.adapters.base import Discovered, FetchOutcome, policy_fetch
from kenya_data_engine.data.checks import check_observations
from kenya_data_engine.data.models import CheckReport, Observation, SeriesSpec
from kenya_data_engine.data.store import AddResult, BlobStore, SeriesStore
from kenya_data_engine.errors import ConfigError, EngineError
from kenya_data_engine.home import EngineHome

__all__ = ["CatalogEntry", "FetchOutcome", "fetch_series", "load_catalog"]


class CatalogEntry(_Strict):
    key: str
    adapter: Literal["worldbank", "sdmx", "listing"]
    title: str
    publisher: str
    tier: Literal[1, 2, 3, 4]
    enabled: bool = True
    note: str | None = None
    spec: SeriesSpec | None = None  # None for document-only entries (e.g. kenyalaw.gazette)
    params: dict[str, Any] = Field(default_factory=dict)


def _describe(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors(include_url=False)
    )


def load_catalog(home: EngineHome) -> dict[str, CatalogEntry]:
    """Packaged defaults, with `<home>/catalog.yaml` merged over them by key (field by field)."""
    packaged = resources.files("kenya_data_engine").joinpath("defaults/catalog.yaml")
    raw = {
        str(k): v
        for k, v in _load_yaml(
            packaged.read_text(encoding="utf-8"), "defaults/catalog.yaml"
        ).items()
    }
    if home.catalog_path.exists():
        user = _load_yaml(home.catalog_path.read_text(encoding="utf-8"), str(home.catalog_path))
        for key, over in user.items():
            base = raw.get(str(key))
            raw[str(key)] = (
                {**base, **over} if isinstance(base, dict) and isinstance(over, dict) else over
            )
    out: dict[str, CatalogEntry] = {}
    for key, fields in raw.items():
        if not isinstance(fields, dict):
            raise ConfigError(f"catalog entry {key}: must be a mapping of fields")
        fields = {**fields, "key": key}
        if isinstance(fields.get("spec"), dict):
            fields["spec"] = {**fields["spec"], "key": key}
        try:
            out[key] = CatalogEntry.model_validate(fields)
        except ValidationError as exc:
            raise ConfigError(
                f"catalog entry {key}: {_describe(exc)}",
                hint="fix it in catalog.yaml (adapters: worldbank, sdmx, listing)",
            ) from exc
    return out


def get_entry(home: EngineHome, key: str) -> CatalogEntry:
    catalog = load_catalog(home)
    if key not in catalog:
        raise EngineError(f"unknown series `{key}`", hint="run `engine data list` for the keys")
    return catalog[key]


def _merge_reports(reports: list[tuple[str, CheckReport]]) -> CheckReport | None:
    if not reports:
        return None
    failures = [f"{url}: {f}" for url, r in reports for f in r.failures]
    status: Literal["accepted", "quarantined"] = (
        "quarantined" if any(r.status == "quarantined" for _, r in reports) else "accepted"
    )
    return CheckReport(
        status=status,
        failures=failures,
        warnings=[w for _, r in reports for w in r.warnings],
        checked=sum(r.checked for _, r in reports),
    )


def _newest_first(items: list[Discovered]) -> list[Discovered]:
    dated = sorted(
        (i for i in items if i.published), key=lambda i: i.published or date.min, reverse=True
    )
    return dated + [i for i in items if not i.published]


async def _extract(
    adapter: Any, entry: CatalogEntry, item: Discovered, content: bytes, sha: str, ctx: RunContext
) -> tuple[list[Observation], list[str]]:
    """Observations and rejects; adapters without an `extract` method have no rejects."""
    extract = getattr(adapter, "extract", None)
    if extract is not None:
        result: tuple[list[Observation], list[str]] = await extract(entry, item, content, sha, ctx)
        return result
    return await adapter.observations(entry, item, content, sha, ctx), []


async def fetch_series(key: str, ctx: RunContext, *, limit: int = 12) -> FetchOutcome:
    """discover → policy fetch → blob → observations → checks → store.

    Items are fetched sequentially (at most `limit`, newest first) and applied oldest first so
    continuity checks compare each release with the one before it. A failing item is recorded
    in `error` and the rest still run; a quarantined item keeps its blob and adds nothing.
    """
    from kenya_data_engine.data.adapters import ADAPTERS

    entry = get_entry(ctx.home, key)
    if not entry.enabled:
        raise EngineError(
            f"series `{key}` is disabled" + (f": {entry.note}" if entry.note else ""),
            hint="run `engine catalog probe` on it, then set `enabled: true` in your catalog.yaml",
        )
    adapter = ADAPTERS[entry.adapter]
    try:
        found = _newest_first(await adapter.discover(entry, ctx))
    except Exception as exc:
        return FetchOutcome(
            key=key,
            discovered=0,
            fetched=0,
            added=None,
            report=None,
            error=ctx.tracer.redact(f"discovery failed: {exc}"),
        )
    if entry.spec is None:  # document-only: nothing to extract
        return FetchOutcome(
            key=key, discovered=len(found), fetched=0, added=None, report=None, error=None
        )
    blobs, store = BlobStore(ctx.home.blobs_dir), SeriesStore(ctx.home.db_path)
    errors: list[str] = []
    reports: list[tuple[str, CheckReport]] = []
    fetched = 0
    totals = AddResult(0, 0, 0)
    for item in reversed(found[:limit]):
        try:
            res = await policy_fetch(item.url, ctx, "item", getattr(adapter, "headers", None))
            sha = blobs.put(res.content)
            fetched += 1
            obs, rejects = await _extract(adapter, entry, item, res.content, sha, ctx)
            if not obs and not rejects:
                raise EngineError("no observations extracted")
            report = check_observations(obs, entry.spec, store.latest(key), rejects)
            reports.append((item.url, report))
            if report.status == "accepted":
                added = store.add(obs)
                totals = AddResult(*(a + b for a, b in zip(totals, added, strict=True)))
        except Exception as exc:
            errors.append(ctx.tracer.redact(f"{item.url}: {exc}"))
    return FetchOutcome(
        key=key,
        discovered=len(found),
        fetched=fetched,
        added=totals,
        report=_merge_reports(reports),
        error="; ".join(errors) or None,
    )
