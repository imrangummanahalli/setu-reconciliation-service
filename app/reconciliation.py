"""Reconciliation queries — summaries and discrepancy detection.

All aggregation happens in SQL (GROUP BY / COUNT / SUM), never in Python
loops, so these scale with the DB rather than with app memory.
"""
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from .models import Transaction

VALID_GROUP_BY = {"merchant", "date", "status"}


def summary(db: Session, group_by: list[str]) -> list[dict]:
    """Reconciliation summary grouped by any of merchant/date/status.

    Returns count of transactions and summed amount per group.
    """
    cols = []
    labels = []
    for g in group_by:
        if g == "merchant":
            cols.append(Transaction.merchant_id)
            labels.append("merchant_id")
        elif g == "status":
            cols.append(Transaction.status)
            labels.append("status")
        elif g == "date":
            # Truncate the first-event timestamp to a calendar day.
            date_col = func.date(Transaction.first_event_at)
            cols.append(date_col)
            labels.append("date")

    stmt = select(
        *cols,
        func.count(Transaction.transaction_id).label("transaction_count"),
        func.coalesce(func.sum(Transaction.amount), 0.0).label("total_amount"),
    ).group_by(*cols).order_by(*cols)

    rows = db.execute(stmt).all()
    out = []
    for r in rows:
        rec: dict = {}
        for i, label in enumerate(labels):
            val = r[i]
            rec[label] = str(val) if label == "date" and val is not None else val
        rec["transaction_count"] = r[len(labels)]
        rec["total_amount"] = float(r[len(labels) + 1] or 0.0)
        out.append(rec)
    return out


def discrepancies(db: Session, merchant_id: str | None = None) -> list[dict]:
    """Transactions whose payment and settlement states are inconsistent.

    Rules (each maps to an example in the assignment):
      1. processed_not_settled — processed, not failed, but never settled.
      2. settled_after_failed  — a settlement exists for a failed payment.
      3. settled_not_processed — settled without ever being processed
                                  (conflicting / out-of-order state).
      4. stuck_initiated       — only ever initiated; no terminal state.
    """
    t = Transaction

    reason = _reason_case()
    stmt = (
        select(
            t.transaction_id,
            t.merchant_id,
            t.status,
            t.amount,
            t.currency,
            t.first_event_at,
            t.last_event_at,
            reason.label("reason"),
        )
        .where(
            or_(
                # 1. processed but never settled (and not failed)
                (t.has_processed) & (~t.has_settled) & (~t.has_failed),
                # 2. settled for a failed payment
                (t.has_settled) & (t.has_failed),
                # 3. settled without processed
                (t.has_settled) & (~t.has_processed),
                # 4. stuck at initiated only
                (t.has_initiated)
                & (~t.has_processed)
                & (~t.has_failed)
                & (~t.has_settled),
            )
        )
        .order_by(t.merchant_id, t.first_event_at)
    )
    if merchant_id:
        stmt = stmt.where(t.merchant_id == merchant_id)

    rows = db.execute(stmt).all()
    return [
        {
            "transaction_id": r.transaction_id,
            "merchant_id": r.merchant_id,
            "status": r.status,
            "amount": r.amount,
            "currency": r.currency,
            "reason": r.reason,
            "first_event_at": r.first_event_at,
            "last_event_at": r.last_event_at,
        }
        for r in rows
    ]


def _reason_case():
    """SQL CASE assigning a human-readable reason. First match wins."""
    from sqlalchemy import case

    t = Transaction
    return case(
        ((t.has_settled) & (t.has_failed), "settled_after_failed"),
        ((t.has_settled) & (~t.has_processed), "settled_not_processed"),
        (
            (t.has_processed) & (~t.has_settled) & (~t.has_failed),
            "processed_not_settled",
        ),
        (
            (t.has_initiated)
            & (~t.has_processed)
            & (~t.has_failed)
            & (~t.has_settled),
            "stuck_initiated",
        ),
        else_="unknown",
    )
