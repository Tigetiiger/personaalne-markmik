"""Form 4 (and 4/A) ownership XML -> insider transactions. Ported from stock-oracle.

Element names follow the SEC "EDGAR Ownership XML Technical Specification"
(ownershipDocument, schema X0508 and earlier). Values sit in <value> children,
and a missing value is often replaced by a <footnoteId>, e.g. a price given
only as a weighted-average range in a footnote.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from .models import InsiderTransaction


class Form4Error(ValueError):
    pass


@dataclass(frozen=True)
class Form4:
    document_type: str
    issuer_cik: str | None
    issuer_ticker: str | None
    period_of_report: date | None
    owners: tuple[str, ...]
    transactions: tuple[InsiderTransaction, ...]

    @property
    def amendment(self) -> bool:
        return self.document_type.endswith("/A")


def _strip_namespaces(root: ET.Element) -> None:
    for element in root.iter():
        if isinstance(element.tag, str) and "}" in element.tag:
            element.tag = element.tag.split("}", 1)[1]


def _text(parent: ET.Element | None, path: str) -> str | None:
    if parent is None:
        return None
    node = parent.find(path)
    if node is None or node.text is None:
        return None
    return node.text.strip() or None


def _value(parent: ET.Element | None, path: str) -> str | None:
    """Text of `path/value`, falling back to `path` itself (older filings)."""
    return _text(parent, f"{path}/value") or _text(parent, path)


def _decimal(raw: str | None) -> Decimal | None:
    if raw is None:
        return None
    try:
        value = Decimal(raw.replace(",", "").replace("$", ""))
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        # Some filers append a timezone offset, e.g. "2026-08-27-05:00".
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _flag(raw: str | None) -> bool:
    return (raw or "").strip().lower() in {"1", "true", "y", "yes"}


def _role(owner: ET.Element) -> str | None:
    rel = owner.find("reportingOwnerRelationship")
    if rel is None:
        return None
    roles: list[str] = []
    if _flag(_text(rel, "isDirector")):
        roles.append("Director")
    if _flag(_text(rel, "isOfficer")):
        title = _text(rel, "officerTitle")
        roles.append(f"Officer ({title})" if title else "Officer")
    if _flag(_text(rel, "isTenPercentOwner")):
        roles.append("10% Owner")
    if _flag(_text(rel, "isOther")):
        other = _text(rel, "otherText")
        roles.append(f"Other ({other})" if other else "Other")
    return "; ".join(roles) or None


def parse_form4(xml: str | bytes) -> Form4:
    raw = xml.encode() if isinstance(xml, str) else xml
    if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        # Ownership XML never declares a DTD; entity expansion has no place in it.
        raise Form4Error("DTD in a Form 4")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise Form4Error(f"not well-formed XML: {exc}") from exc
    _strip_namespaces(root)
    if root.tag != "ownershipDocument":
        raise Form4Error(f"expected <ownershipDocument>, got <{root.tag}>")

    document_type = _text(root, "documentType") or ""
    if document_type not in {"4", "4/A"}:
        raise Form4Error(f"not a Form 4: documentType={document_type!r}")
    amendment = document_type == "4/A"

    owners = root.findall("reportingOwner")
    if not owners:
        raise Form4Error("no reportingOwner")
    # A joint filing lists several owners for the same transactions; attribute
    # them to the first and keep the others' names on the side, so shares are
    # not counted once per co-filer.
    names = tuple(_text(o, "reportingOwnerId/rptOwnerName") or "unknown" for o in owners)
    primary = owners[0]
    insider_name = names[0]
    insider_cik = _text(primary, "reportingOwnerId/rptOwnerCik")
    insider_role = _role(primary)
    period = _date(_text(root, "periodOfReport"))

    transactions: list[InsiderTransaction] = []
    for derivative, path in (
        (False, "nonDerivativeTable/nonDerivativeTransaction"),
        (True, "derivativeTable/derivativeTransaction"),
    ):
        for row in root.findall(path):
            trade_date = _date(_value(row, "transactionDate")) or period
            code = _text(row, "transactionCoding/transactionCode")
            shares = _decimal(_value(row, "transactionAmounts/transactionShares"))
            if trade_date is None or code is None or shares is None:
                # Unusable row (e.g. a derivative row reporting only a value, no
                # share count); skip it rather than inventing numbers.
                continue
            direct = (_value(row, "ownershipNature/directOrIndirectOwnership") or "D") == "D"
            transactions.append(
                InsiderTransaction(
                    insider_name=insider_name,
                    insider_cik=insider_cik,
                    insider_role=insider_role,
                    security_title=_value(row, "securityTitle") or "",
                    trade_date=trade_date,
                    code=code.upper(),
                    acquired_disposed=_value(
                        row, "transactionAmounts/transactionAcquiredDisposedCode"
                    ),
                    shares=shares,
                    price=_decimal(_value(row, "transactionAmounts/transactionPricePerShare")),
                    shares_after=_decimal(
                        _value(row, "postTransactionAmounts/sharesOwnedFollowingTransaction")
                    ),
                    direct=direct,
                    derivative=derivative,
                    amendment=amendment,
                    underlying_shares=_decimal(
                        _value(row, "underlyingSecurity/underlyingSecurityShares")
                    )
                    if derivative
                    else None,
                    exercise_price=_decimal(_value(row, "conversionOrExercisePrice"))
                    if derivative
                    else None,
                )
            )

    return Form4(
        document_type=document_type,
        issuer_cik=_text(root, "issuer/issuerCik"),
        issuer_ticker=_text(root, "issuer/issuerTradingSymbol"),
        period_of_report=period,
        owners=names,
        transactions=tuple(transactions),
    )
