# Spike S4: Yahoo's chart endpoint without yfinance (2026-09-29)

Question (docs/AKTSIAD-PLAN.md, finding 4): can Märkmik read bars, dividends and splits from
Yahoo's JSON directly with `httpx`, from inside the read-only `worker` container, instead of
yfinance (which brings pandas and numpy and writes a cache under `~/.cache`)?

**Answer: yes.**

- `GET https://query1.finance.yahoo.com/v8/finance/chart/<symbol>?range=5y&interval=1d&events=div,splits&includeAdjustedClose=true`
  with a browser-like `User-Agent` answered 200 without cookies or a crumb for AAPL, VWCE.DE, NVO
  and BANB.SW (about 120–140 KB each, ~1,255 bars).
- `meta` carries `currency`, `exchangeName` (NMS, GER, NYQ, EBS), `fullExchangeName`,
  `instrumentType` (EQUITY, ETF), `exchangeTimezoneName`, `longName`, `regularMarketPrice`.
- Timestamps are the session open; the bar's date is that instant in `exchangeTimezoneName`.
- `indicators.quote[0]` has open/high/low/close/volume, `indicators.adjclose[0].adjclose` the
  adjusted close. Closes are split-adjusted. Some days have `null` values (VWCE.DE: 3 in 5 years):
  skip those bars.
- `events.dividends` / `events.splits` are keyed by timestamp: `{amount, date}` and
  `{numerator, denominator, splitRatio}`. NVO's 2:1 (2023-09-20) and BANB.SW's 5:1 (2022-05-05)
  are there.
- Compared with stock-oracle's stored yfinance bars (AAPL, NVO on 2022-01-03, 2023-09-15,
  2023-09-25, 2026-09-25): close and adjusted close equal after rounding to 7 significant
  digits (stock-oracle's own rounding); dividends equal. The last day's volume differs slightly
  (Yahoo revises it).
- Intraday: `range=1d&interval=5m` works (79 bars mid-session), with `currentTradingPeriod`.
- Search: `https://query2.finance.yahoo.com/v1/finance/search?q=novo nordisk&quotesCount=5&newsCount=0`
  gives symbol, exchange and quoteType (NVO NYQ, NOVO-B.CO CPH, …).

Consequence: `app/stocks/providers/yahoo.py` uses these two endpoints with `httpx`; no yfinance,
pandas or numpy. Pace calls (one per 2 s, as stock-oracle did) and treat a 429 as rate-limited.
