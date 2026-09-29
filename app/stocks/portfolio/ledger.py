"""Replay transactions into FIFO lots, applying splits at read time."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date

from .model import (
    ZERO,
    InvalidTransaction,
    Lot,
    OversellError,
    Position,
    Realisation,
    Transaction,
    TxKind,
)


@dataclass(frozen=True, slots=True)
class Book:
    """The state of an account at the end of `as_of`."""

    as_of: date
    # Open positions only (quantity > 0).
    positions: Mapping[str, Position]
    realisations: tuple[Realisation, ...]
    # The transactions replayed, in replay order.
    transactions: tuple[Transaction, ...]


def in_order(transactions: Iterable[Transaction], as_of: date) -> list[Transaction]:
    """Transactions up to and including `as_of`, by timestamp. Ties keep input
    order, so a buy entered before a same-second split stays before it."""
    kept = [tx for tx in transactions if tx.trade_date <= as_of]
    return sorted(kept, key=lambda tx: tx.ts)


def build_book(transactions: Iterable[Transaction], as_of: date) -> Book:
    ordered = in_order(transactions, as_of)
    lots: dict[str, list[Lot]] = {}
    currencies: dict[str, str] = {}
    realisations: list[Realisation] = []

    for tx in ordered:
        if tx.kind not in {TxKind.BUY, TxKind.SELL, TxKind.SPLIT, TxKind.DIVIDEND}:
            continue
        assert tx.instrument is not None
        known = currencies.setdefault(tx.instrument, tx.currency)
        if known != tx.currency:
            raise InvalidTransaction(
                f"{tx.kind} of {tx.instrument} at {tx.ts} is in {tx.currency},"
                f" earlier transactions were in {known}"
            )
        held = lots.setdefault(tx.instrument, [])
        if tx.kind is TxKind.BUY:
            held.append(Lot(tx.instrument, tx.trade_date, tx.qty or ZERO, tx.gross + tx.fee))
        elif tx.kind is TxKind.SELL:
            realisations.append(_sell_fifo(held, tx))
        elif tx.kind is TxKind.SPLIT:
            assert tx.ratio is not None
            ratio = tx.ratio
            held[:] = [Lot(lot.instrument, lot.opened, lot.qty * ratio, lot.cost) for lot in held]

    positions = {
        instrument: Position(instrument, currencies[instrument], tuple(held))
        for instrument, held in lots.items()
        if held
    }
    return Book(as_of, positions, tuple(realisations), tuple(ordered))


def _sell_fifo(held: list[Lot], tx: Transaction) -> Realisation:
    assert tx.instrument is not None and tx.qty is not None
    available = sum((lot.qty for lot in held), ZERO)
    if tx.qty > available:
        raise OversellError(
            f"selling {tx.qty} {tx.instrument} at {tx.ts}, but only {available} held"
        )
    remaining = tx.qty
    consumed: list[Lot] = []
    while remaining > 0:
        lot = held[0]
        if lot.qty <= remaining:
            consumed.append(held.pop(0))
            remaining -= lot.qty
            continue
        # Partial lot: the remainder keeps cost - portion so the two halves add
        # up to the original cost exactly.
        portion = lot.cost * remaining / lot.qty
        consumed.append(Lot(lot.instrument, lot.opened, remaining, portion))
        held[0] = Lot(lot.instrument, lot.opened, lot.qty - remaining, lot.cost - portion)
        remaining = ZERO
    return Realisation(
        instrument=tx.instrument,
        currency=tx.currency,
        sold=tx.trade_date,
        qty=tx.qty,
        proceeds=tx.gross - tx.fee,
        lots=tuple(consumed),
    )


def positions(transactions: Iterable[Transaction], as_of: date) -> Mapping[str, Position]:
    return build_book(transactions, as_of).positions
