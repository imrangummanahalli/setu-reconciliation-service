"""SQLAlchemy ORM models.

Schema design (3 tables):

  merchants     — one row per merchant (dimension table for grouping/joins).
  events        — immutable, append-only event log. event_id is the PK, which
                  is what gives us idempotency for free: a duplicate insert
                  violates the PK and is ignored.
  transactions  — materialized per-transaction state, derived from events.
                  We keep boolean lifecycle flags (initiated/processed/failed/
                  settled) so that reconciliation & discrepancy queries are
                  plain, index-friendly SQL instead of Python loops.

Why a materialized `transactions` table instead of computing state on read?
Listing, filtering, sorting and reconciliation all key off transaction status.
Recomputing status from the event log on every read would mean a GROUP BY over
the full events table per request. Instead we fold each event into the
transaction row at ingest time (cheap, O(1) per event) and index the columns
the APIs filter/sort on.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

# Lifecycle stages ordered by "progress". The transaction's headline status is
# the furthest stage reached, with terminal states (settled/failed) winning.
STATUS_INITIATED = "initiated"
STATUS_PROCESSED = "processed"
STATUS_FAILED = "failed"
STATUS_SETTLED = "settled"

EVENT_TYPES = {
    "payment_initiated",
    "payment_processed",
    "payment_failed",
    "settled",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Merchant(Base):
    __tablename__ = "merchants"

    merchant_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)

    transactions: Mapped[list["Transaction"]] = relationship(back_populates="merchant")


class Transaction(Base):
    __tablename__ = "transactions"

    transaction_id: Mapped[str] = mapped_column(String, primary_key=True)
    merchant_id: Mapped[str] = mapped_column(
        String, ForeignKey("merchants.merchant_id"), nullable=False
    )
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str | None] = mapped_column(String, nullable=True)

    # Derived headline status (see status_from_flags).
    status: Mapped[str] = mapped_column(String, nullable=False, default=STATUS_INITIATED)

    # Lifecycle flags — the atoms of reconciliation. Set once, never unset.
    has_initiated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_processed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_failed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_settled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # first event timestamp (business time) and last update (ingest time).
    first_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    merchant: Mapped["Merchant"] = relationship(back_populates="transactions")
    events: Mapped[list["Event"]] = relationship(
        back_populates="transaction", order_by="Event.timestamp"
    )

    __table_args__ = (
        # Listing filters/sorts most commonly by merchant, status and time.
        Index("ix_txn_merchant_status", "merchant_id", "status"),
        Index("ix_txn_status", "status"),
        Index("ix_txn_first_event_at", "first_event_at"),
        # Partial-ish flag indexes help the discrepancy scans.
        Index("ix_txn_flags", "has_processed", "has_settled", "has_failed"),
    )


class Event(Base):
    __tablename__ = "events"

    # event_id as PK == idempotency. Re-ingesting the same event is a no-op.
    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    transaction_id: Mapped[str] = mapped_column(
        String, ForeignKey("transactions.transaction_id"), nullable=False
    )
    merchant_id: Mapped[str] = mapped_column(String, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str | None] = mapped_column(String, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    transaction: Mapped["Transaction"] = relationship(back_populates="events")

    __table_args__ = (
        Index("ix_event_txn", "transaction_id"),
        Index("ix_event_type", "event_type"),
    )


def status_from_flags(
    has_initiated: bool, has_processed: bool, has_failed: bool, has_settled: bool
) -> str:
    """Headline status = furthest lifecycle stage, terminal states winning.

    Order-independent: it depends only on *which* events exist, not their
    arrival order, so out-of-order or duplicate delivery can't corrupt it.
    """
    if has_settled:
        return STATUS_SETTLED
    if has_failed:
        return STATUS_FAILED
    if has_processed:
        return STATUS_PROCESSED
    return STATUS_INITIATED
