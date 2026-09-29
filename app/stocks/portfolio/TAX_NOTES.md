# Estonian investment account (investeerimiskonto): tax notes

Researched 2026-09-21. The rules are in the Income Tax Act (Tulumaksuseadus,
TuMS) § 17^2; EMTA (Maksu- ja Tolliamet) applies them through table 6.5 of
the individual's income tax return (Form A). **Check this again before relying
on it for a real return**, especially the rate table for any year after 2026.

What `investment_account.py` implements is marked against each item:
**[V]** verified against the source cited, **[A]** an assumption or a
simplification of mine.

## Sources

| # | Source | Date / validity |
|---|---|---|
| S1 | EMTA, *Securities and investment account* — https://www.emta.ee/en/private-client/taxes-and-payment/taxable-income/securities-and-investment-account | page updated 03.02.2026 |
| S2 | EMTA, *Tax rates* — https://www.emta.ee/en/private-client/taxes-and-payment/declaration-income/tax-rates | page updated 11.11.2025 |
| S3 | EMTA, Form A completion instructions for 2022, points 59–68 (table 6.5) — https://www.emta.ee/sites/default/files/documents/2023-01/vormi_a_taitmise_juhend_2022.pdf | tax year 2022; newest instructions PDF I could find |
| S4 | EMTA, Form A 2025 (the form itself) — https://www.emta.ee/sites/default/files/documents/2026-02/vorm_a_2025_ee_en.pdf | tax year 2025, published 2026-02 |
| S5 | Rahandusministeerium (Ministry of Finance), *Mis on investeerimiskonto?*, 15.12.2010 — https://www.fin.ee/media/747/download | written for 2011 when the system started; mechanics unchanged since, per S3/S4 |

The Riigi Teataja text of TuMS (https://www.riigiteataja.ee/akt/129032012028)
would not render without JavaScript, so § 17^2 was **not read directly**; its
content here comes from S1, S3 and S5, which quote it.

## Verified facts

1. **[V] What is taxed.** "Investeerimiskontolt väljamakse tegemisel
   maksustatakse summa, mille võrra kõikidelt investeerimiskontodelt tehtud
   väljamaksed ületavad selle väljamakse järel kõikidele investeerimiskontodele
   tehtud sissemaksete jääki." (S3 p. 59). A withdrawal is taxed on the amount
   by which it exceeds the contribution balance.
2. **[V] Across all accounts, date by date.** The balance is computed over all
   of the taxpayer's investment accounts together, after each contribution or
   withdrawal in time order (S1, S3 p. 59, S5).
3. **[V] Table 6.5 formula** (S3 pp. 66–67, column headers confirmed in S4):
   with A = contribution balance before the entry, B = contribution,
   C = withdrawal:
   - taxable amount = C − B − A, if positive (else 0), computed after each withdrawal;
   - amount carried forward = the negative of (C − B − A) if that is negative,
     else 0; it becomes A on the next line and carries over into the next year.
   So the balance never goes below zero, and a deposit after a taxed withdrawal
   starts again from zero.
4. **[V] Contributions** (S1, S3 p. 62): money transferred into the account;
   taxed interest and taxed dividends received into it (S5 example 7: an
   Estonian dividend of 15 000 paid into the account is declared as a
   contribution). **Sale proceeds are not contributions**, nor is money moved
   from another of the taxpayer's investment accounts. An existing bank account
   taken into use as an investment account contributes its balance on that day.
   Documented acquisition/disposal costs paid from *outside* the account also
   count as contributions (S3 p. 62).
5. **[V] Withdrawals** (S1, S3 pp. 59, 63): every debit from the account except
   paying for financial assets. Also: margin-loan interest, the closing balance
   when the account is closed, and income from financial assets not paid
   promptly into the account. Buying crypto counts as a withdrawal. Moving money
   to another of one's own investment accounts and currency conversion are
   **not** withdrawals.
6. **[V] Currency.** Contributions and withdrawals are declared in euros at the
   exchange rate on the day the account is credited or debited (S5 §2.3).
7. **[V] Foreign withholding.** Foreign dividends/interest are taxed in Estonia
   with a credit for foreign tax; S1's example: 10% withheld abroad leaves 10%
   to pay in Estonia through 2024 and 12% from 2025, i.e. the Estonian rate
   minus the withholding rate.
8. **[V] Rate.** Income tax rate 20% for 2024, 22% for 2025, 22% for 2026 (S2).
   No rate for 2027 was published on S2 at the time of writing, so the default
   table stops at 2026 and asking for a later year raises `UnknownTaxYear`.
9. **[V] Timing.** Tax on the taxable amount of year N is declared in the
   return for year N and paid in year N+1 (S5 example 2).

## Worked examples reproduced as tests

From S5 (amounts in "rahaühik", currency units), both in
`tests/stocks/portfolio/test_portfolio_investment_account.py`:

- **Two accounts, five entries** (S5 p. 4, starting balance 120): contributions
  15 and 10, withdrawals 55, 10, 90 → carried forward 135, 145, 90, 80, and a
  taxable amount of **10** on the last withdrawal.
- **Example 2, two years** (S5 pp. 5–6): deposit 100, buy 100, sell 110, buy
  105, withdraw 5 in 2011 → balance 95, nothing taxable; 2012 sell for 120 and
  withdraw 120 → taxable **25**.

## Assumptions and simplifications

- **[A] Dividends count as contributions at the net amount credited** (gross
  minus foreign withholding). S1 says taxed dividends are declared "as a
  contribution"; a secondary summary of EMTA's guidance says the *net* amount
  received may be declared. The Estonian top-up tax on the dividend is paid
  separately through the return and is not modelled as an account flow.
- **[A] Every dividend and interest payment is treated as already taxed**, so
  every one is a contribution. Untaxed interest would strictly *not* be a
  contribution (S1: "non-taxed interest ... not declared"). This app has no
  way to tell them apart.
- **[A] Foreign dividend top-up** = max(0, gross × rate − withheld) in EUR at
  the payment date, per S1's "rate minus withholding" example. It ignores
  treaty caps and the per-country limits on the credit. Estonian-company
  dividends (taxed at company level) would owe nothing; the code cannot tell,
  so the top-up is reported separately and should be read as foreign-only.
- **[A] Standalone fees (custody, account fees) are withdrawals.** By the
  literal rule (fact 5) any debit that does not buy a financial asset is a
  withdrawal. This is the conservative reading (it can only increase tax).
  Commissions recorded on a buy or sell are part of the acquisition cost or a
  reduction of proceeds and are neither contributions nor withdrawals.
- **[A] Deferred tax liability** = max(0, value of account in EUR −
  contribution balance) × the rate for the `as_of` year, where value = cash +
  holdings at market. It ignores sale costs and that the tax is actually paid a
  year later.
- **[A] Transfers between two of the user's own investment accounts** are not
  modelled (there is no transfer transaction kind). Pass the transactions of
  all investment accounts together to get the correct combined balance, and do
  not record an internal transfer as withdrawal + deposit.
- **[A] Opening balance.** A starting contribution balance can be passed in
  for an account whose earlier history isn't recorded (e.g. from last year's
  return).
