"""FastAPI application — payment reconciliation service."""
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from . import reconciliation
from .db import Base, engine, get_db
from .ingest import IngestError, ingest_events
from .models import Transaction
from .schemas import (
    DiscrepancyRow,
    EventIn,
    IngestResult,
    Page,
    SummaryRow,
    TransactionDetail,
    TransactionOut,
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Setu Payment Reconciliation Service",
    description=(
        "Ingests payment lifecycle events idempotently, tracks transaction "
        "state, and reports reconciliation summaries and discrepancies."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/events", response_model=IngestResult, status_code=201)
def post_events(
    payload: EventIn | list[EventIn],
    db: Session = Depends(get_db),
) -> IngestResult:
    """Ingest one event or a batch. Idempotent on event_id."""
    events = [payload] if isinstance(payload, EventIn) else payload
    if not events:
        raise HTTPException(status_code=400, detail="empty event payload")
    try:
        result = ingest_events(db, [e.model_dump() for e in events])
    except IngestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return IngestResult(**result)


SORTABLE = {
    "first_event_at": Transaction.first_event_at,
    "last_event_at": Transaction.last_event_at,
    "updated_at": Transaction.updated_at,
    "amount": Transaction.amount,
    "status": Transaction.status,
}


@app.get("/transactions", response_model=Page)
def list_transactions(
    db: Session = Depends(get_db),
    merchant_id: str | None = None,
    status: str | None = None,
    start: datetime | None = Query(None, description="filter first_event_at >= start"),
    end: datetime | None = Query(None, description="filter first_event_at <= end"),
    sort_by: str = Query("first_event_at"),
    order: Literal["asc", "desc"] = "desc",
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> Page:
    """List transactions with filtering, sorting and pagination (all in SQL)."""
    if sort_by not in SORTABLE:
        raise HTTPException(
            status_code=422,
            detail=f"sort_by must be one of {sorted(SORTABLE)}",
        )

    filters = []
    if merchant_id:
        filters.append(Transaction.merchant_id == merchant_id)
    if status:
        filters.append(Transaction.status == status)
    if start:
        filters.append(Transaction.first_event_at >= start)
    if end:
        filters.append(Transaction.first_event_at <= end)

    total = db.scalar(
        select(func.count()).select_from(Transaction).where(*filters)
    )

    sort_col = SORTABLE[sort_by]
    sort_col = sort_col.desc() if order == "desc" else sort_col.asc()

    rows = db.scalars(
        select(Transaction)
        .where(*filters)
        .order_by(sort_col)
        .limit(limit)
        .offset(offset)
    ).all()

    return Page(
        items=[TransactionOut.model_validate(r) for r in rows],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


@app.get("/transactions/{transaction_id}", response_model=TransactionDetail)
def get_transaction(
    transaction_id: str, db: Session = Depends(get_db)
) -> TransactionDetail:
    """Full transaction detail: state, merchant, and ordered event history."""
    txn = db.scalars(
        select(Transaction)
        .where(Transaction.transaction_id == transaction_id)
        .options(selectinload(Transaction.events), selectinload(Transaction.merchant))
    ).first()
    if txn is None:
        raise HTTPException(status_code=404, detail="transaction not found")
    return TransactionDetail.model_validate(txn)


@app.get("/reconciliation/summary", response_model=list[SummaryRow])
def reconciliation_summary(
    db: Session = Depends(get_db),
    group_by: str = Query(
        "merchant,status",
        description="comma-separated dimensions: merchant, date, status",
    ),
) -> list[SummaryRow]:
    """Aggregated counts and amounts grouped by chosen dimensions."""
    dims = [d.strip() for d in group_by.split(",") if d.strip()]
    invalid = [d for d in dims if d not in reconciliation.VALID_GROUP_BY]
    if invalid or not dims:
        raise HTTPException(
            status_code=422,
            detail=f"group_by must be a subset of {sorted(reconciliation.VALID_GROUP_BY)}",
        )
    rows = reconciliation.summary(db, dims)
    return [SummaryRow(**r) for r in rows]


@app.get("/reconciliation/discrepancies", response_model=list[DiscrepancyRow])
def reconciliation_discrepancies(
    db: Session = Depends(get_db),
    merchant_id: str | None = None,
) -> list[DiscrepancyRow]:
    """Transactions with inconsistent payment vs. settlement state."""
    return [DiscrepancyRow(**r) for r in reconciliation.discrepancies(db, merchant_id)]
