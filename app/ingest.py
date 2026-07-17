"""Idempotent event ingestion.

Idempotency strategy: `events.event_id` is the primary key. We attempt to
insert each event; if the event_id already exists, it is a duplicate and is
skipped. Only genuinely new events mutate transaction state, and because the
derived status is a pure function of the *set* of event types seen
(status_from_flags), re-deriving is safe and order-independent regardless.
"""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    EVENT_TYPES,
    Event,
    Merchant,
    Transaction,
    status_from_flags,
)


class IngestError(ValueError):
    """Raised for structurally invalid events (bad type, etc.)."""


_TYPE_TO_FLAG = {
    "payment_initiated": "has_initiated",
    "payment_processed": "has_processed",
    "payment_failed": "has_failed",
    "settled": "has_settled",
}


def ingest_events(db: Session, events: list[dict]) -> dict:
    """Ingest a batch of events in a single transaction.

    Returns counts of accepted vs. duplicate events and the set of affected
    transaction ids. Validation errors abort the whole batch (nothing is
    committed) so a caller never gets a partial write.
    """
    # Validate up front.
    for e in events:
        if e["event_type"] not in EVENT_TYPES:
            raise IngestError(
                f"unknown event_type '{e['event_type']}' "
                f"(allowed: {sorted(EVENT_TYPES)})"
            )

    accepted = 0
    duplicates = 0
    affected: set[str] = set()

    # Pre-load existing event_ids in this batch to detect duplicates cheaply
    # (both against the DB and within the batch itself).
    incoming_ids = [e["event_id"] for e in events]
    existing_ids = set(
        db.scalars(select(Event.event_id).where(Event.event_id.in_(incoming_ids))).all()
    )
    seen_in_batch: set[str] = set()

    # Cache merchant/transaction rows touched in this batch.
    merchant_cache: dict[str, Merchant] = {}
    txn_cache: dict[str, Transaction] = {}

    for e in events:
        eid = e["event_id"]
        if eid in existing_ids or eid in seen_in_batch:
            duplicates += 1
            continue
        seen_in_batch.add(eid)

        ts = _as_dt(e["timestamp"])
        merchant_id = e["merchant_id"]
        txn_id = e["transaction_id"]

        # Upsert merchant (dimension row).
        merchant = merchant_cache.get(merchant_id)
        if merchant is None:
            merchant = db.get(Merchant, merchant_id)
            if merchant is None:
                merchant = Merchant(
                    merchant_id=merchant_id,
                    name=e.get("merchant_name") or merchant_id,
                )
                db.add(merchant)
            merchant_cache[merchant_id] = merchant
        # Keep the latest known name.
        if e.get("merchant_name"):
            merchant.name = e["merchant_name"]

        # Upsert transaction and fold this event into its state.
        txn = txn_cache.get(txn_id)
        if txn is None:
            txn = db.get(Transaction, txn_id)
            if txn is None:
                txn = Transaction(
                    transaction_id=txn_id,
                    merchant_id=merchant_id,
                    amount=e.get("amount"),
                    currency=e.get("currency"),
                    first_event_at=ts,
                    last_event_at=ts,
                )
                db.add(txn)
            txn_cache[txn_id] = txn

        # Set the lifecycle flag for this event type.
        setattr(txn, _TYPE_TO_FLAG[e["event_type"]], True)

        # amount/currency: fill if missing (initiated usually carries them).
        if txn.amount is None and e.get("amount") is not None:
            txn.amount = e.get("amount")
        if txn.currency is None and e.get("currency") is not None:
            txn.currency = e.get("currency")

        # Track business-time bounds. Coerce stored values to aware UTC first,
        # since some backends (SQLite) hand back naive datetimes.
        first = _ensure_aware(txn.first_event_at)
        last = _ensure_aware(txn.last_event_at)
        if first is None or ts < first:
            txn.first_event_at = ts
        if last is None or ts > last:
            txn.last_event_at = ts

        # Recompute headline status from flags (order-independent).
        txn.status = status_from_flags(
            txn.has_initiated, txn.has_processed, txn.has_failed, txn.has_settled
        )

        db.add(
            Event(
                event_id=eid,
                transaction_id=txn_id,
                merchant_id=merchant_id,
                event_type=e["event_type"],
                amount=e.get("amount"),
                currency=e.get("currency"),
                timestamp=ts,
            )
        )
        accepted += 1
        affected.add(txn_id)

    db.commit()
    return {
        "accepted": accepted,
        "duplicates": duplicates,
        "transaction_ids": sorted(affected),
    }


def _as_dt(value) -> datetime:
    if isinstance(value, datetime):
        return _ensure_aware(value)
    from dateutil import parser

    return _ensure_aware(parser.isoparse(value))


def _ensure_aware(value: datetime | None) -> datetime | None:
    """Coerce naive datetimes (as returned by SQLite) to UTC-aware."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
