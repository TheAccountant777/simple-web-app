"""Series adapters, by `CatalogEntry.adapter`."""

from kenya_data_engine.data.adapters.base import Adapter, Discovered, FetchOutcome
from kenya_data_engine.data.adapters.listing import ListingAdapter
from kenya_data_engine.data.adapters.sdmx import SdmxAdapter
from kenya_data_engine.data.adapters.worldbank import WorldBankAdapter

ADAPTERS: dict[str, Adapter] = {
    "worldbank": WorldBankAdapter(),
    "sdmx": SdmxAdapter(),
    "listing": ListingAdapter(),
}

__all__ = ["ADAPTERS", "Adapter", "Discovered", "FetchOutcome"]
