"""Fakes for the stocks-night tests: a clock that moves only when told, and stand-ins for the AI
package (research.py, verdict.py) that return exactly the contract's detail shape
{done, skipped, failed, stopped} (docs/CONTRACTS.md, "stocks-ai")."""

import types
from datetime import datetime, time, timedelta, timezone

from app.util import TALLINN


def local(day, hh, mm=0, fold=0):
    """Tallinn wall clock on `day` as aware UTC."""
    return datetime.combine(day, time(hh, mm, fold=fold), tzinfo=TALLINN).astimezone(timezone.utc)


class FakeClock:
    def __init__(self, start):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)

    def sleep(self, seconds):
        self.slept = getattr(self, "slept", []) + [seconds]
        self.t += timedelta(seconds=seconds)


class FakeAI:
    """The AI package's night functions over a list of symbols. `per_stock`: how long one stock
    takes on the clock; `stop_at`: {symbol: reason} makes the call stop there, as
    runner.AIUnavailable would; `fail`: symbols that fail alone. Every call is recorded."""

    def __init__(self, symbols, *, per_stock=timedelta(minutes=5), stop_at=None, fail=()):
        self.symbols, self.per_stock = list(symbols), per_stock
        self.stop_at, self.fail = dict(stop_at or {}), set(fail)
        self.calls = []

    def _phase(self, name, now, deadline):
        self.calls.append({"step": name, "at": now(), "deadline": deadline})
        out = {"done": [], "skipped": [], "failed": [], "stopped": None}
        for i, symbol in enumerate(self.symbols):
            if out["stopped"]:
                out["skipped"].append({"symbol": symbol, "reason": out["stopped"]})
                continue
            if now() >= deadline:
                out["stopped"] = "Öö aeg sai läbi"
                out["skipped"].append({"symbol": symbol, "reason": out["stopped"]})
                continue
            if symbol in self.stop_at:
                out["stopped"] = self.stop_at[symbol]
                out["skipped"].append({"symbol": symbol, "reason": out["stopped"]})
                continue
            if hasattr(now, "advance"):
                now.advance(seconds=self.per_stock.total_seconds())
            if symbol in self.fail:
                out["failed"].append({"symbol": symbol, "error": "vigane vastus"})
            else:
                out["done"].append(symbol)
        return out

    def modules(self):
        research = types.ModuleType("app.stocks.research")
        research.extract_night = lambda conn, run_id, now, deadline: self._phase("extract", now, deadline)
        research.deep_night = lambda conn, run_id, today, now, deadline: self._phase("research", now, deadline)
        verdict = types.ModuleType("app.stocks.verdict")
        verdict.verdict_night = lambda conn, run_id, today, now, deadline: self._phase("verdict", now, deadline)
        return research, verdict
