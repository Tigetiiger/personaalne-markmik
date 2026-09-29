import logging
from collections.abc import Callable
from datetime import date

from .types import Bar, CorporateAction, InstrumentRef, PriceProvider, ProviderError, Quote, SymbolMatch

log = logging.getLogger(__name__)


class FallbackProvider:
    """Asks `primary` first and `fallback` when the primary raises any `ProviderError` (error,
    rate limit, not covered).

    `served` records, per method, which provider answered the last call; the returned records
    also carry their own `source`.
    """

    def __init__(self, primary: PriceProvider, fallback: PriceProvider) -> None:
        self.primary = primary
        self.fallback = fallback
        self.served: dict[str, str] = {}

    @property
    def name(self) -> str:
        return f"{self.primary.name}+{self.fallback.name}"

    def _call[T](self, method: str, call: Callable[[PriceProvider], T]) -> T:
        try:
            result = call(self.primary)
            self.served[method] = self.primary.name
            return result
        except ProviderError as exc:
            log.info("%s failed (%s), falling back to %s", method, exc, self.fallback.name)
            # Called inside the handler so a fallback failure keeps the primary's as context.
            result = call(self.fallback)
        self.served[method] = self.fallback.name
        return result

    def daily_bars(self, ref: InstrumentRef, start: date, end: date) -> list[Bar]:
        return self._call("daily_bars", lambda p: p.daily_bars(ref, start, end))

    def quote(self, ref: InstrumentRef) -> Quote:
        return self._call("quote", lambda p: p.quote(ref))

    def corporate_actions(self, ref: InstrumentRef, start: date, end: date) -> list[CorporateAction]:
        return self._call("corporate_actions", lambda p: p.corporate_actions(ref, start, end))

    def search(self, query: str) -> list[SymbolMatch]:
        return self._call("search", lambda p: p.search(query))
