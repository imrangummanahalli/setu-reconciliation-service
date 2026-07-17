"""End-to-end tests exercising idempotency, filtering and reconciliation.

Runs against an isolated SQLite file so it never touches dev data.
"""
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

# Point the app at a throwaway DB before importing it.
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


client = TestClient(app)


def _event(**kw):
    base = {
        "event_id": "e1",
        "event_type": "payment_initiated",
        "transaction_id": "t1",
        "merchant_id": "m1",
        "merchant_name": "Acme",
        "amount": 100.0,
        "currency": "INR",
        "timestamp": "2026-01-01T10:00:00+00:00",
    }
    base.update(kw)
    return base


def test_ingest_and_fetch():
    r = client.post("/events", json=_event())
    assert r.status_code == 201
    assert r.json()["accepted"] == 1

    r = client.get("/transactions/t1")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "initiated"
    assert body["merchant"]["name"] == "Acme"
    assert len(body["events"]) == 1


def test_idempotent_duplicate_event():
    client.post("/events", json=_event())
    r = client.post("/events", json=_event())  # exact same event_id
    assert r.json()["accepted"] == 0
    assert r.json()["duplicates"] == 1

    # Only one event stored; state uncorrupted.
    body = client.get("/transactions/t1").json()
    assert len(body["events"]) == 1
    assert body["status"] == "initiated"


def test_full_lifecycle_status_progression():
    client.post("/events", json=_event(event_id="e1", event_type="payment_initiated"))
    client.post("/events", json=_event(event_id="e2", event_type="payment_processed"))
    client.post("/events", json=_event(event_id="e3", event_type="settled"))
    body = client.get("/transactions/t1").json()
    assert body["status"] == "settled"
    assert body["has_processed"] and body["has_settled"]


def test_out_of_order_events_are_order_independent():
    # Deliver settled first, then initiated — status must still be settled.
    client.post("/events", json=_event(event_id="e3", event_type="settled"))
    client.post("/events", json=_event(event_id="e1", event_type="payment_initiated"))
    body = client.get("/transactions/t1").json()
    assert body["status"] == "settled"


def test_batch_ingest_with_internal_duplicate():
    batch = [
        _event(event_id="a", transaction_id="tb"),
        _event(event_id="a", transaction_id="tb"),  # dup within batch
        _event(event_id="b", transaction_id="tb", event_type="payment_processed"),
    ]
    r = client.post("/events", json=batch)
    assert r.json()["accepted"] == 2
    assert r.json()["duplicates"] == 1


def test_filter_and_pagination():
    for i in range(5):
        client.post(
            "/events",
            json=_event(
                event_id=f"e{i}",
                transaction_id=f"t{i}",
                merchant_id="m2" if i % 2 else "m1",
            ),
        )
    r = client.get("/transactions", params={"merchant_id": "m1", "limit": 2})
    body = r.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert all(it["merchant_id"] == "m1" for it in body["items"])


def test_invalid_event_type_rejected():
    r = client.post("/events", json=_event(event_type="bogus"))
    assert r.status_code == 422


def test_discrepancy_processed_not_settled():
    client.post("/events", json=_event(event_id="e1", event_type="payment_initiated"))
    client.post("/events", json=_event(event_id="e2", event_type="payment_processed"))
    r = client.get("/reconciliation/discrepancies")
    rows = r.json()
    assert any(
        row["transaction_id"] == "t1" and row["reason"] == "processed_not_settled"
        for row in rows
    )


def test_discrepancy_settled_after_failed():
    client.post("/events", json=_event(event_id="e1", event_type="payment_initiated"))
    client.post("/events", json=_event(event_id="e2", event_type="payment_failed"))
    client.post("/events", json=_event(event_id="e3", event_type="settled"))
    rows = client.get("/reconciliation/discrepancies").json()
    assert any(row["reason"] == "settled_after_failed" for row in rows)


def test_summary_grouping():
    client.post("/events", json=_event(event_id="e1", event_type="settled"))
    client.post(
        "/events",
        json=_event(event_id="e2", transaction_id="t2", event_type="payment_failed"),
    )
    rows = client.get(
        "/reconciliation/summary", params={"group_by": "merchant,status"}
    ).json()
    assert len(rows) == 2
    statuses = {r["status"] for r in rows}
    assert statuses == {"settled", "failed"}
