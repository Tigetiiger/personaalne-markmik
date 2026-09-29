"""Estonian investment account. Sources and caveats: app/stocks/portfolio/TAX_NOTES.md."""

from decimal import Decimal

import pytest

from app.stocks.portfolio import (
    DEFAULT_TAX_RATES,
    EntryKind,
    PortfolioError,
    Transaction,
    UnknownTaxYear,
    contribution_balance,
    contribution_ledger,
    deferred_tax,
    tax_rate,
    year_summary,
)
from portfolio_support import D, Fx, at, d, no_fx

# The 2010 Ministry of Finance examples are in "currency units"; 2011-2012 are
# not in the default table, so tests that need a rate pass one. The rate
# itself is not part of the official examples.
RATES_2011 = {2011: D("0.21"), 2012: D("0.21")}


def test_official_example_two_accounts_five_entries() -> None:
    """Rahandusministeerium, "Mis on investeerimiskonto?" (2010), p. 4 (S5)."""
    txs = [
        Transaction.deposit(at("2011-02-12"), D(15)),  # account 12345
        Transaction.deposit(at("2011-03-13"), D(10)),  # account 67890
        Transaction.withdrawal(at("2011-05-16"), D(55)),
        Transaction.withdrawal(at("2011-08-09"), D(10)),
        Transaction.withdrawal(at("2011-10-07"), D(90)),
    ]
    entries = contribution_ledger(txs, d("2011-12-31"), no_fx, opening_balance=D(120))
    assert [(e.balance_before, e.amount, e.taxable, e.carried_forward) for e in entries] == [
        (D(120), D(15), D(0), D(135)),
        (D(135), D(10), D(0), D(145)),
        (D(145), D(55), D(0), D(90)),
        (D(90), D(10), D(0), D(80)),
        (D(80), D(90), D(10), D(0)),
    ]
    summary = year_summary(txs, 2011, no_fx, RATES_2011, opening_balance=D(120))
    assert summary.taxable_amount == D(10)  # "Kokku: 10"
    assert summary.tax_due == D("2.1")


def test_official_example_2_across_two_years() -> None:
    """S5 example 2 (pp. 5-6): gains reinvested in 2011, taxed when withdrawn in 2012."""
    txs = [
        Transaction.deposit(at("2011-02-03"), D(100)),
        Transaction.buy(at("2011-02-15"), "CDE", D(1), D(100), "EUR"),
        Transaction.sell(at("2011-09-21"), "CDE", D(1), D(110), "EUR"),
        Transaction.buy(at("2011-10-07"), "DEF", D(1), D(105), "EUR"),
        Transaction.withdrawal(at("2011-12-23"), D(5)),
        Transaction.sell(at("2012-02-12"), "DEF", D(1), D(120), "EUR"),
        Transaction.withdrawal(at("2012-03-13"), D(120)),
    ]
    y2011 = year_summary(txs, 2011, no_fx, RATES_2011)
    assert [(e.balance_before, e.amount, e.carried_forward) for e in y2011.entries] == [
        (D(0), D(100), D(100)),
        (D(100), D(5), D(95)),
    ]
    assert (y2011.taxable_amount, y2011.rate, y2011.tax_due) == (D(0), None, D(0))

    y2012 = year_summary(txs, 2012, no_fx, RATES_2011)
    assert y2012.opening_balance == D(95)
    [line] = y2012.entries
    assert (line.balance_before, line.amount, line.taxable) == (D(95), D(120), D(25))
    assert y2012.taxable_amount == D(25)
    assert y2012.closing_balance == D(0)


def test_withdrawal_below_contribution_balance_is_not_taxed() -> None:
    txs = [
        Transaction.deposit(at("2026-01-05"), D(1000)),
        Transaction.withdrawal(at("2026-02-05"), D(400)),
    ]
    summary = year_summary(txs, 2026, no_fx)
    assert summary.taxable_amount == 0
    assert summary.closing_balance == D(600)
    assert summary.tax_due == 0


def test_withdrawal_above_contribution_balance_is_taxed_on_the_excess() -> None:
    txs = [
        Transaction.deposit(at("2026-01-05"), D(1000)),
        Transaction.buy(at("2026-01-06"), "VWCE", D(10), D(100), "EUR"),
        Transaction.sell(at("2026-06-01"), "VWCE", D(10), D(130), "EUR"),
        Transaction.withdrawal(at("2026-06-02"), D(1300)),
    ]
    summary = year_summary(txs, 2026, no_fx)
    assert summary.taxable_amount == D(300)
    assert summary.rate == D("0.22")
    assert summary.tax_due == D(66)
    assert summary.closing_balance == 0


def test_deposit_after_a_taxed_withdrawal_starts_from_zero() -> None:
    txs = [
        Transaction.deposit(at("2026-01-05"), D(1000)),
        Transaction.buy(at("2026-01-06"), "VWCE", D(10), D(100), "EUR"),
        Transaction.sell(at("2026-06-01"), "VWCE", D(10), D(130), "EUR"),
        Transaction.withdrawal(at("2026-06-02"), D(1300)),
        Transaction.deposit(at("2026-07-01"), D(500)),
        Transaction.withdrawal(at("2026-08-01"), D(200)),
    ]
    entries = contribution_ledger(txs, d("2026-12-31"), no_fx)
    assert [(e.kind, e.taxable, e.carried_forward) for e in entries] == [
        (EntryKind.CONTRIBUTION, D(0), D(1000)),
        (EntryKind.WITHDRAWAL, D(300), D(0)),
        (EntryKind.CONTRIBUTION, D(0), D(500)),
        (EntryKind.WITHDRAWAL, D(0), D(300)),
    ]


def test_reinvested_us_dividends_are_contributions_at_net_eur() -> None:
    fx = Fx({"USD": {"2026-01-01": "0.90"}})
    txs = [
        Transaction.deposit(at("2026-01-05"), D(1000)),
        Transaction.buy(at("2026-01-06"), "KO", D(10), D(60), "USD"),
        Transaction.dividend(at("2026-04-01"), "KO", D(20), "USD", tax_withheld=D(3)),
        Transaction.buy(at("2026-04-02"), "KO", D("0.25"), D(68), "USD"),
    ]
    # 17 USD net x 0.90.
    assert contribution_balance(txs, d("2026-12-31"), fx) == D("1015.3")
    summary = year_summary(txs, 2026, fx)
    assert (summary.dividends_gross, summary.dividends_withheld) == (D(18), D("2.7"))
    # 18 x 22% = 3.96 Estonian tax, less 2.70 withheld in the US.
    assert summary.dividend_top_up == D("1.26")
    assert summary.contributions == D("1015.3")


def test_dividend_withheld_above_the_estonian_rate_has_no_top_up() -> None:
    txs = [Transaction.dividend(at("2026-04-01"), "X", D(100), "EUR", tax_withheld=D(30))]
    assert year_summary(txs, 2026, no_fx).dividend_top_up == 0


def test_foreign_currency_flows_convert_at_their_own_date() -> None:
    fx = Fx({"USD": {"2026-01-01": "0.90", "2026-06-01": "0.80"}})
    txs = [
        Transaction.deposit(at("2026-01-05"), D(1000), "USD"),  # 900 EUR
        Transaction.withdrawal(at("2026-06-05"), D(1000), "USD"),  # 800 EUR
    ]
    assert contribution_balance(txs, d("2026-12-31"), fx) == D(100)


def test_standalone_fee_is_a_withdrawal_but_commission_is_not() -> None:
    txs = [
        Transaction.deposit(at("2026-01-05"), D(1000)),
        Transaction.buy(at("2026-01-06"), "VWCE", D(1), D(100), "EUR", fee=D(5)),
        Transaction.account_fee(at("2026-02-01"), D(2)),
        Transaction.interest(at("2026-03-01"), D(1), "EUR"),
    ]
    assert contribution_balance(txs, d("2026-12-31"), no_fx) == D(999)


def test_multi_year_summaries_chain() -> None:
    txs = [
        Transaction.deposit(at("2024-03-01"), D(1000)),
        Transaction.buy(at("2024-03-02"), "VWCE", D(10), D(100), "EUR"),
        Transaction.sell(at("2025-05-01"), "VWCE", D(5), D(140), "EUR"),
        Transaction.withdrawal(at("2025-05-02"), D(700)),
        Transaction.deposit(at("2026-01-10"), D(200)),
        Transaction.sell(at("2026-05-01"), "VWCE", D(5), D(150), "EUR"),
        Transaction.withdrawal(at("2026-05-02"), D(500)),
    ]
    y2024, y2025, y2026 = (year_summary(txs, year, no_fx) for year in (2024, 2025, 2026))
    assert (y2024.closing_balance, y2024.taxable_amount) == (D(1000), D(0))
    assert (y2025.opening_balance, y2025.closing_balance, y2025.taxable_amount) == (
        D(1000),
        D(300),
        D(0),
    )
    # 300 + 200 = 500 balance, withdraw 500: nothing taxable yet.
    assert (y2026.opening_balance, y2026.contributions, y2026.withdrawals) == (
        D(300),
        D(200),
        D(500),
    )
    assert (y2026.closing_balance, y2026.taxable_amount) == (D(0), D(0))
    # Cash left: 1000 - 1000 + 700 - 700 + 200 + 750 - 500 = 450, all of it untaxed gain.
    deferred = deferred_tax(txs, d("2026-06-30"), {}, no_fx)
    assert deferred.account_value == D(450)
    assert (deferred.contribution_balance, deferred.taxable_gain) == (D(0), D(450))
    assert deferred.tax == D(99)


def test_deferred_tax_if_everything_were_withdrawn_today() -> None:
    txs = [
        Transaction.deposit(at("2026-01-05"), D(1000)),
        Transaction.buy(at("2026-01-06"), "VWCE", D(10), D(100), "EUR"),
    ]
    up = deferred_tax(txs, d("2026-06-30"), {"VWCE": D(130)}, no_fx)
    assert (up.account_value, up.contribution_balance, up.taxable_gain) == (
        D(1300),
        D(1000),
        D(300),
    )
    assert (up.rate, up.tax) == (D("0.22"), D(66))

    down = deferred_tax(txs, d("2026-06-30"), {"VWCE": D(90)}, no_fx)
    assert (down.taxable_gain, down.tax) == (D(0), D(0))


def test_rates() -> None:
    assert tax_rate(2024) == D("0.20")
    assert tax_rate(2025) == D("0.22")
    assert tax_rate(2026) == D("0.22")
    assert tax_rate(2027, {2027: D("0.24")}) == D("0.24")
    with pytest.raises(UnknownTaxYear, match="2027"):
        tax_rate(2027)
    with pytest.raises(TypeError):
        DEFAULT_TAX_RATES[2027] = Decimal("0.5")  # type: ignore[index]


def test_unknown_year_only_matters_when_tax_is_due() -> None:
    quiet = [Transaction.deposit(at("2027-01-05"), D(10))]
    assert year_summary(quiet, 2027, no_fx).rate is None
    taxed = [*quiet, Transaction.withdrawal(at("2027-01-06"), D(20))]
    with pytest.raises(UnknownTaxYear):
        year_summary(taxed, 2027, no_fx)
    with pytest.raises(UnknownTaxYear):
        deferred_tax(quiet, d("2027-06-30"), {}, no_fx)


def test_year_without_entries_carries_the_balance() -> None:
    txs = [Transaction.deposit(at("2024-01-05"), D(10))]
    summary = year_summary(txs, 2025, no_fx)
    assert (summary.opening_balance, summary.closing_balance, summary.entries) == (D(10), D(10), ())
    assert contribution_balance([], d("2026-01-01"), no_fx, opening_balance=D(7)) == D(7)


def test_negative_opening_balance_is_rejected() -> None:
    with pytest.raises(PortfolioError, match="cannot be negative"):
        contribution_ledger([], d("2026-01-01"), no_fx, opening_balance=D(-1))
