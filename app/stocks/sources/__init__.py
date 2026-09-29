"""News and filings for the followed stocks (docs/AKTSIAD-PLAN.md, "News and filings"): SEC EDGAR
(8-K, 10-Q/10-K, Form 4), Finnhub company news and earnings calendar, RSS (Yahoo per ticker,
Google News per company name, IR feeds) and Alpha Vantage news sentiment, ported from
stock-oracle's sources/. Interface: docs/CONTRACTS.md, "stocks-sources"."""

from . import models, text
from .pipeline import attention_z, count_docs, ingest, poll_filings

__all__ = ["attention_z", "count_docs", "ingest", "models", "poll_filings", "text"]
