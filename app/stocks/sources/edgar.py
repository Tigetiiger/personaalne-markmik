"""SEC EDGAR: 8-K, 10-Q, 10-K and Form 4 filings for one issuer. Ported from stock-oracle.

Endpoints (https://www.sec.gov/search-filings/edgar-application-programming-interfaces):
- https://www.sec.gov/files/company_tickers.json   ticker -> CIK
- https://data.sec.gov/submissions/CIK##########.json   recent filings, columnar
- https://www.sec.gov/Archives/edgar/data/<cik>/<accession-no-dashes>/<primaryDocument>

Fair access (https://www.sec.gov/os/accessing-edgar-data): at most 10 requests a second, and a
User-Agent naming the requester with a contact e-mail (vault `sec_user_agent`)."""

import logging
import time
from collections.abc import Callable, Collection
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .base import HttpFetcher, RateLimiter, Sleep, SourceError
from .form4 import Form4, Form4Error, parse_form4
from .models import DocumentIn, InsiderTransaction, InstrumentRef
from .text import html_to_text, parse_iso

log = logging.getLogger(__name__)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"

DEFAULT_FORMS = frozenset({"8-K", "8-K/A", "10-Q", "10-Q/A", "10-K", "10-K/A", "4", "4/A"})
# The hourly poll between nights (poll_filings): no periodic reports.
POLL_FORMS = frozenset({"8-K", "8-K/A", "4", "4/A"})
FORM4 = frozenset({"4", "4/A"})

# acceptanceDateTime carries a "Z" suffix but, judging by after-close filings landing at 16:xx,
# it is US Eastern wall time. Unverified against an official statement (stock-oracle's finding).
EDGAR_TZ = ZoneInfo("America/New_York")


def pad_cik(cik: str | int) -> str:
    return f"{int(cik):010d}"


class EdgarSource:
    name = "edgar"

    def __init__(self, client: httpx.Client, *, user_agent: str, limiter: RateLimiter | None = None,
                 sleep: Sleep = time.sleep, forms: Collection[str] = DEFAULT_FORMS):
        if "@" not in user_agent:
            raise ValueError("SEC requires a User-Agent with a contact e-mail, e.g. 'app you@x.com'")
        self.http = HttpFetcher(client, limiter or RateLimiter.per_second(10, sleep=sleep), sleep=sleep,
                                headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
        self.forms = frozenset(forms)
        self._ciks: dict[str, str] | None = None

    def cik_for(self, ticker: str) -> str | None:
        if self._ciks is None:
            data = self.http.get(TICKERS_URL).json()
            rows = data.values() if isinstance(data, dict) else []
            self._ciks = {str(row["ticker"]).upper(): pad_cik(row["cik_str"])
                          for row in rows if isinstance(row, dict) and "ticker" in row and "cik_str" in row}
        # EDGAR writes share classes with a dash (BRK-B); users often type a dot.
        key = ticker.upper()
        return self._ciks.get(key) or self._ciks.get(key.replace(".", "-"))

    def fetch(self, instrument: InstrumentRef, since: datetime, now: datetime, *,
              known: Callable[[str], bool] = lambda accession: False) -> list[DocumentIn]:
        """Filings accepted between since and now. `known(accession)` = already stored with its
        text: skipped, so a repeated poll does not download the same bodies again."""
        cik = pad_cik(instrument.cik) if instrument.cik else self.cik_for(instrument.ticker)
        if cik is None:
            log.info("edgar: no CIK for %s (ETF or non-US listing?)", instrument.ticker)
            return []
        submissions = self.http.get(SUBMISSIONS_URL.format(cik=cik)).json()
        docs: list[DocumentIn] = []
        for filing in recent_filings(submissions):
            if filing["form"] not in self.forms:
                continue
            published = filing_time(filing)
            if published is None or published < since or published > now or known(filing["accessionNumber"]):
                continue
            docs.append(self._document(instrument, cik, filing, published))
        return docs

    def _document(self, instrument: InstrumentRef, cik: str, filing: dict[str, Any], published: datetime) -> DocumentIn:
        form = filing["form"]
        accession = filing["accessionNumber"]
        primary = filing.get("primaryDocument") or ""
        if form in FORM4:
            # The listed primaryDocument is the XSL-rendered view ("xslF345X05/form4.xml"); the
            # raw XML is the same name one level up.
            primary = primary.rsplit("/", 1)[-1]
        url = ARCHIVE_URL.format(cik=int(cik), accession=accession.replace("-", ""), document=primary) if primary else None
        meta: dict[str, Any] = {"form": form, "cik": cik, "accession": accession}
        if filing.get("items"):
            meta["items"] = [item for item in str(filing["items"]).split(",") if item]
        if filing.get("reportDate"):
            meta["report_date"] = filing["reportDate"]
        description = filing.get("primaryDocDescription") or form
        title = f"{instrument.name} {form}: {description}"
        body: str | None = None
        trades: tuple[InsiderTransaction, ...] = ()
        if url is not None:
            try:
                response = self.http.get(url)
            except SourceError as exc:
                # The filing is still worth a document; the body is fetched again on a later run.
                log.warning("edgar: body of %s unavailable: %s", accession, exc)
                meta["body_error"] = str(exc)
            else:
                if form in FORM4:
                    try:
                        parsed = parse_form4(response.content)
                    except Form4Error as exc:
                        log.warning("edgar: unparseable Form 4 %s: %s", accession, exc)
                        meta["parse_error"] = str(exc)
                        body = html_to_text(response.text) or None
                    else:
                        trades = parsed.transactions
                        title = f"{instrument.name} {form}: {form4_headline(parsed)}"
                        body = form4_summary(parsed)
                else:
                    body = html_to_text(response.text) or None
        return DocumentIn(source=self.name, external_id=accession, url=url, title=title, body=body, author=None,
                          published_at=published, instruments=(instrument.ticker,), language="en", meta=meta,
                          insider_trades=trades)


def recent_filings(submissions: Any) -> list[dict[str, Any]]:
    """The columnar `filings.recent` block as one dict per filing."""
    try:
        recent = submissions["filings"]["recent"]
        accessions = recent["accessionNumber"]
    except (KeyError, TypeError) as exc:
        raise SourceError(f"unexpected submissions JSON: missing {exc}") from exc
    columns = {key: value for key, value in recent.items() if isinstance(value, list)}
    rows: list[dict[str, Any]] = []
    for i in range(len(accessions)):
        row = {key: (values[i] if i < len(values) else None) for key, values in columns.items()}
        if row.get("form") and row.get("accessionNumber"):
            rows.append(row)
    return rows


def filing_time(filing: dict[str, Any]) -> datetime | None:
    accepted = filing.get("acceptanceDateTime")
    if isinstance(accepted, str) and accepted:
        wall = parse_iso(accepted.rstrip("Z").split(".")[0], assume=EDGAR_TZ)
        if wall is not None:
            return wall
    filed = filing.get("filingDate")
    if isinstance(filed, str):
        try:
            day = date.fromisoformat(filed)
        except ValueError:
            return None
        return datetime(day.year, day.month, day.day, tzinfo=EDGAR_TZ).astimezone(UTC)
    return None


def form4_headline(form: Form4) -> str:
    owner = form.owners[0]
    buys = sum(t.shares for t in form.transactions if t.code == "P")
    sells = sum(t.shares for t in form.transactions if t.code == "S")
    parts = []
    if buys:
        parts.append(f"bought {buys:,} shares")
    if sells:
        parts.append(f"sold {sells:,} shares")
    action = " and ".join(parts) or "reported other transactions"
    prefix = "Amended: " if form.amendment else ""
    return f"{prefix}{owner} {action}"


def form4_summary(form: Form4) -> str:
    lines = [f"Reporting owner(s): {', '.join(form.owners)}"]
    if form.transactions and form.transactions[0].insider_role:
        lines.append(f"Role: {form.transactions[0].insider_role}")
    for t in form.transactions:
        price = f" at {t.price}" if t.price is not None else ""
        after = f", {t.shares_after} held after" if t.shares_after is not None else ""
        kind = "derivative" if t.derivative else "non-derivative"
        lines.append(f"{t.trade_date.isoformat()} code {t.code} ({t.acquired_disposed or '?'}) {t.shares} "
                     f"{t.security_title}{price}{after} [{kind}, {'direct' if t.direct else 'indirect'}]")
    if not form.transactions:
        lines.append("No transactions reported (holdings only).")
    return "\n".join(lines)
