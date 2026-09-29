"""The portfolio maths raises English PortfolioErrors (ported unchanged from stock-oracle); the
owner sees them in Estonian. estonian(exc, symbols) gives the text for the form, the CSV preview
and the Portfell page."""

import re
from datetime import date, datetime

from ...util import TALLINN
from .investment_account import UnknownTaxYear
from .model import OversellError

# Transaction.__post_init__ problems, after "<kind> at <ts>: ".
PROBLEMS = {
    "timestamp must be timezone-aware": "Aeg on vigane.",
    "fee and tax_withheld must not be negative": "Tasu ja kinnipeetud maks ei tohi olla negatiivsed.",
    "instrument is required": "Vali väärtpaber.",
    "must not name an instrument": "Sellel tehinguliigil pole väärtpaberit.",
    "qty must be positive": "Kogus puudub või pole positiivne.",
    "price must be zero or positive": "Hind puudub või on negatiivne.",
    "amount and ratio do not apply to trades": "Ostul ja müügil pole summat ega suhet.",
    "qty and price only apply to buy and sell": "Kogus ja hind on ainult ostul ja müügil.",
    "ratio must be positive": "Suhe puudub või pole positiivne.",
    "ratio only applies to splits": "Suhe on ainult splitil.",
    "amount must be positive": "Summa puudub või pole positiivne.",
    "amount does not apply": "Sellel tehinguliigil pole summat.",
    "tax_withheld exceeds the gross amount": "Kinnipeetud maks on suurem kui brutosumma.",
    "tax_withheld only applies to dividends and interest": "Kinnipeetud maks on ainult dividendil ja intressil.",
    "fee only applies to buy and sell; record other fees as kind=fee": "Tasu käib ostu või müügi juurde; muu tasu lisa eraldi tehinguna „Tasu“.",
}
_OVERSELL = re.compile(r"selling (?P<qty>\S+) (?P<key>\S+) at (?P<ts>.+), but only (?P<held>\S+) held$")
_CURRENCY = re.compile(r"\S+ of (?P<key>\S+) at .+ is in (?P<ccy>\w+), earlier transactions were in (?P<known>\w+)$")
_ISO = re.compile(r"currency must be a 3-letter ISO code, got (?P<got>.*)$")
# Returns and valuation problems (returns.py, view.PriceBook): {symbol} and {day} are filled in.
_RETURNS = (
    (re.compile(r"value appeared from nothing on (?P<day>\S+)"),
     "konto väärtus tekkis {day} tühjast kohast. Kas mõni sissemakse on lisamata?"),
    (re.compile(r"negative starting value on (?P<day>\S+)"),
     "konto väärtus oli {day} negatiivne. Kas mõni sissemakse on lisamata?"),
    (re.compile(r"XIRR needs at least one positive"), "rahaga kaalutud tootluseks on vaja nii sisse- kui väljaminekut."),
    (re.compile(r"XIRR did not find a rate"), "rahaga kaalutud tootlust ei õnnestunud leida."),
    (re.compile(r"no price for instrument (?P<key>\S+) on (?P<day>\S+)"), "{symbol} hind {day} kohta puudub."),
)


def _day(ts):
    try:
        if len(ts) == 10:
            return date.fromisoformat(ts).strftime("%d.%m.%Y")
        return datetime.fromisoformat(ts).astimezone(TALLINN).strftime("%d.%m.%Y")
    except ValueError:
        return ts


def _qty(text):
    return text.rstrip("0").rstrip(".") if "." in text else text


def estonian(exc, symbols=None):
    """The Estonian text of a PortfolioError; `symbols` maps instrument keys to tickers."""
    symbols = symbols or {}
    text = str(exc)
    if isinstance(exc, OversellError) and (m := _OVERSELL.search(text)):
        name = symbols.get(m["key"], m["key"])
        return f"Müüd {_qty(m['qty'])} {name}, aga {_day(m['ts'])} seisuga on sul ainult {_qty(m['held'])}."
    if isinstance(exc, UnknownTaxYear) and (m := re.search(r"for (\d{4})", text)):
        return f"Aasta {m[1]} maksumäär puudub. Lisa see Seadetes (Aktsiad), kui oled EMTA juhendi üle vaadanud."
    if m := _CURRENCY.search(text):
        name = symbols.get(m["key"], m["key"])
        return f"{name} tehingud on seni olnud valuutas {m['known']}, see on {m['ccy']}."
    for pattern, template in _RETURNS:
        if m := pattern.search(text):
            found = m.groupdict()
            day = found.get("day")
            return template.format(day=_day(day) if day else "", symbol=symbols.get(found.get("key"), found.get("key")))
    problem = text.split(": ", 1)[-1]
    if problem in PROBLEMS:
        return PROBLEMS[problem]
    if m := _ISO.search(problem):
        return f"Valuuta peab olema kolmetäheline kood (nt EUR), mitte {m['got']}."
    return text[:1].upper() + text[1:] + ("" if text.endswith(".") else ".")
