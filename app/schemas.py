"""Pydantic request/response schemas."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class EventIn(BaseModel):
    event_id: str = Field(..., min_length=1)
    event_type: str
    transaction_id: str = Field(..., min_length=1)
    merchant_id: str = Field(..., min_length=1)
    merchant_name: str | None = None
    amount: float | None = None
    currency: str | None = None
    timestamp: datetime


class IngestResult(BaseModel):
    accepted: int
    duplicates: int
    transaction_ids: list[str]


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_id: str
    event_type: str
    amount: float | None
    currency: str | None
    timestamp: datetime
    received_at: datetime


class MerchantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    merchant_id: str
    name: str


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    transaction_id: str
    merchant_id: str
    amount: float | None
    currency: str | None
    status: str
    has_initiated: bool
    has_processed: bool
    has_failed: bool
    has_settled: bool
    first_event_at: datetime | None
    last_event_at: datetime | None
    updated_at: datetime


class TransactionDetail(TransactionOut):
    merchant: MerchantOut | None = None
    events: list[EventOut] = []


class Page(BaseModel):
    items: list[TransactionOut]
    total: int
    limit: int
    offset: int


class SummaryRow(BaseModel):
    merchant_id: str | None = None
    date: str | None = None
    status: str | None = None
    transaction_count: int
    total_amount: float


class DiscrepancyRow(BaseModel):
    transaction_id: str
    merchant_id: str
    status: str
    amount: float | None
    currency: str | None
    reason: str
    first_event_at: datetime | None
    last_event_at: datetime | None
