"""User-facing error types."""


class EngineError(Exception):
    """An error meant to be shown to the user as a message plus an optional hint."""

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class ConfigError(EngineError):
    """Invalid or missing configuration."""


class BudgetExceeded(EngineError):
    """The run budget has been used up."""


class FetchError(EngineError):
    """A page or feed could not be fetched."""


class SearchError(EngineError):
    """No search provider could answer."""


class ExtractError(EngineError):
    """A document could not be turned into tables."""
