# Setu Payment Reconciliation Service

A lightweight, production-minded backend that ingests payment lifecycle events,
tracks transaction state, and surfaces reconciliation summaries and
discrepancies between payment and settlement status.

Built with **FastAPI + SQLAlchemy**, backed by **SQLite** by default (zero setup)
and **Postgres**-ready via a single environment variable.

---

## Run the project

```bash
cd solutions-engineer
docker compose up --build
```

This builds the app + Postgres, seeds the sample data on first boot (10,355
events → 10,165 stored, 190 duplicates skipped), and serves on
**http://localhost:8000**.

Open the interactive docs at **http://localhost:8000/docs** (Swagger UI) or
import `postman_collection.json`.

---

## Try it out — insert a value and check it

Once the service is running (any of the methods above), you can insert an event
and verify it end-to-end. All examples assume the default `http://localhost:8000`.

### 1. Insert an event (`POST /events`)

```bash
curl -X POST http://localhost:8000/events \
  -H "Content-Type: application/json" \
  -d '{
    "event_id": "demo-1",
    "event_type": "payment_initiated",
    "transaction_id": "demo-txn-1",
    "merchant_id": "merchant_1",
    "merchant_name": "Acme",
    "amount": 1200.50,
    "currency": "INR",
    "timestamp": "2026-05-01T10:00:00+00:00"
  }'
```

Response — the event was stored:

```json
{ "accepted": 1, "duplicates": 0, "transaction_ids": ["demo-txn-1"] }
```

### 2. Check it (`GET /transactions/{id}`)

```bash
curl http://localhost:8000/transactions/demo-txn-1
```

Returns the transaction with its status and event history:

```json
{
  "transaction_id": "demo-txn-1",
  "merchant_id": "merchant_1",
  "amount": 1200.50,
  "status": "initiated",
  "merchant": { "merchant_id": "merchant_1", "name": "Acme" },
  "events": [ { "event_id": "demo-1", "event_type": "payment_initiated", "amount": 1200.50, ... } ]
}
```

### 3. Verify idempotency — insert the *same* event again

```bash
# re-run the exact same POST from step 1
```

Response shows it was recognised as a duplicate and **not** re-inserted:

```json
{ "accepted": 0, "duplicates": 1, "transaction_ids": [] }
```

The transaction still has exactly one event — state is uncorrupted.

### 4. Advance the transaction and watch status change

```bash
# mark it processed
curl -X POST http://localhost:8000/events -H "Content-Type: application/json" -d '{
  "event_id": "demo-2", "event_type": "payment_processed",
  "transaction_id": "demo-txn-1", "merchant_id": "merchant_1",
  "amount": 1200.50, "currency": "INR", "timestamp": "2026-05-01T10:05:00+00:00" }'

# then settle it
curl -X POST http://localhost:8000/events -H "Content-Type: application/json" -d '{
  "event_id": "demo-3", "event_type": "settled",
  "transaction_id": "demo-txn-1", "merchant_id": "merchant_1",
  "amount": 1200.50, "currency": "INR", "timestamp": "2026-05-01T12:00:00+00:00" }'

# check the status is now "settled"
curl http://localhost:8000/transactions/demo-txn-1
```

### 5. Query the aggregates

```bash
# list transactions for a merchant (filtered, paginated, sorted)
curl "http://localhost:8000/transactions?merchant_id=merchant_1&status=settled&limit=5"

# reconciliation summary grouped by merchant + status
curl "http://localhost:8000/reconciliation/summary?group_by=merchant,status"

# transactions with inconsistent payment vs. settlement state
curl "http://localhost:8000/reconciliation/discrepancies"
```

> **Tip:** the same requests are pre-built in `postman_collection.json`, and you
> can run them interactively from the Swagger UI at
> **http://localhost:8000/docs** (click *Try it out* on any endpoint).

---

## Deployment

The repo ships a `render.yaml` blueprint and a `Dockerfile`, so the same image
runs locally and in the cloud.

- **Public deployment:** `<ADD YOUR LIVE URL HERE>` — e.g. `https://setu-reconciliation.onrender.com`
- Health check: `GET /health`
- On [Render](https://render.com): *New → Blueprint → point at this repo*. The
  free plan runs the bundled SQLite image directly. For Postgres, provision a
  managed instance and set `DATABASE_URL` (see below), then run `python seed.py`
  once as a one-off job.

> **Reviewer note:** the service is fully functional locally with the two
> commands above if the hosted URL is unavailable.

---

## Architecture overview

```
        POST /events (single or batch)
                 │
                 ▼
        ┌──────────────────┐     idempotency: events.event_id is the PRIMARY KEY.
        │   ingest.py      │     A duplicate event_id is detected and skipped;
        │  (idempotent)    │     only new events mutate state.
        └──────────────────┘
                 │  folds each event into ...
                 ▼
   ┌───────────┐   ┌───────────────┐   ┌───────────┐
   │ merchants │   │ transactions  │   │  events   │  (append-only log)
   │ dimension │◀──│  materialized │   │ immutable │
   └───────────┘   │  state + flags│   └───────────┘
                   └───────────────┘
                 │
                 ▼
   GET /transactions            GET /reconciliation/summary
   GET /transactions/{id}       GET /reconciliation/discrepancies
   (filter/sort/paginate)       (SQL GROUP BY / flag scans)
```

### Data model (3 tables)

| Table          | Purpose                                                                                     |
| -------------- | ------------------------------------------------------------------------------------------- |
| `merchants`    | Dimension table (one row per merchant), used for grouping and joins.                        |
| `events`       | **Append-only, immutable** event log. `event_id` is the primary key → free idempotency.     |
| `transactions` | **Materialized per-transaction state**, derived from events at ingest time.                 |

The `transactions` table carries four boolean lifecycle flags —
`has_initiated`, `has_processed`, `has_failed`, `has_settled` — plus a derived
headline `status`. This is the key design decision (see tradeoffs).

### How status is derived

`status` is a **pure function of which event types have been seen**, not their
arrival order:

```
settled  > failed  > processed  > initiated
```

(terminal states win). Because it depends on the *set* of events rather than a
sequence of transitions, **out-of-order and duplicate delivery cannot corrupt
it** — re-deriving from the flags always yields the same answer.

---

## Idempotency (not optional)

Three layers guarantee that submitting the same event twice is a no-op:

1. **`event_id` primary key** — the database physically rejects a second row
   with the same id.
2. **Pre-flight duplicate check** — before inserting a batch, we look up which
   `event_id`s already exist and which repeat *within* the batch, so duplicates
   are counted and skipped rather than throwing.
3. **Order-independent state derivation** — even if events arrive out of order
   or more than once, `status` is recomputed from the boolean flags, so state
   never drifts.

The ingest response makes this observable:

```json
{ "accepted": 2, "duplicates": 1, "transaction_ids": ["demo-txn-1"] }
```

Verified against the provided data: 10,355 rows in → **10,165 accepted, 190
duplicates skipped**.

---

## API documentation

Base URL: `http://localhost:8000` (or your deployment). Full interactive spec at
`/docs`.

### `POST /events`
Ingest a single event object **or** a JSON array of events (batch). Idempotent.

Request (single):
```json
{
  "event_id": "b768e3a7-...",
  "event_type": "payment_initiated",
  "transaction_id": "2f86e94c-...",
  "merchant_id": "merchant_2",
  "merchant_name": "FreshBasket",
  "amount": 15248.29,
  "currency": "INR",
  "timestamp": "2026-01-08T12:11:58.085567+00:00"
}
```
`event_type` must be one of `payment_initiated`, `payment_processed`,
`payment_failed`, `settled` (else `422`). Response: `201` with the
`{accepted, duplicates, transaction_ids}` summary above.

### `GET /transactions`
List transactions with filtering, sorting, and pagination — **all executed in SQL**.

| Query param   | Description                                              |
| ------------- | -------------------------------------------------------- |
| `merchant_id` | filter by merchant                                       |
| `status`      | `initiated` \| `processed` \| `failed` \| `settled`      |
| `start`,`end` | date range on `first_event_at` (ISO-8601)                |
| `sort_by`     | `first_event_at` (default), `last_event_at`, `updated_at`, `amount`, `status` |
| `order`       | `asc` \| `desc` (default `desc`)                         |
| `limit`       | 1–500 (default 50)                                       |
| `offset`      | ≥0 (default 0)                                           |

Response: `{ "items": [...], "total": N, "limit": L, "offset": O }`.

### `GET /transactions/{transaction_id}`
Full detail: transaction state + flags, related merchant, and the **ordered
event history** for that transaction. `404` if unknown.

### `GET /reconciliation/summary`
Aggregated counts and summed amounts. `group_by` is a comma-separated subset of
`merchant`, `date`, `status` (default `merchant,status`).

```bash
GET /reconciliation/summary?group_by=merchant,status
```
```json
[ { "merchant_id": "merchant_1", "status": "settled",
    "transaction_count": 530, "total_amount": 13162794.09 }, ... ]
```

### `GET /reconciliation/discrepancies`
Transactions whose payment and settlement states are inconsistent. Optional
`merchant_id` filter. Each row carries a `reason`:

| `reason`                | Meaning                                             |
| ----------------------- | --------------------------------------------------- |
| `processed_not_settled` | payment processed but never settled                 |
| `settled_after_failed`  | a settlement recorded for a failed payment          |
| `settled_not_processed` | settled without ever being processed (out-of-order) |
| `stuck_initiated`       | only initiated; no terminal state reached           |

Against the sample data this returns **665** rows: 380 `processed_not_settled`,
190 `stuck_initiated`, 95 `settled_after_failed`.

---

## Indexes & query design

Aggregation, filtering, and pagination all happen in SQL — never in Python loops.
Indexes are chosen for the exact access patterns the APIs use:

- `ix_txn_merchant_status (merchant_id, status)` — the common list filter.
- `ix_txn_status`, `ix_txn_first_event_at` — status filter and date-range sort.
- `ix_txn_flags (has_processed, has_settled, has_failed)` — discrepancy scans.
- `ix_event_txn (transaction_id)` — pulling a transaction's event history.

Because status is materialized on the `transactions` row, listing and
reconciliation never re-scan the 10k-row event log; they read pre-computed
columns.

---

## Configuration

| Env var        | Default                          | Notes                                    |
| -------------- | -------------------------------- | ---------------------------------------- |
| `DATABASE_URL` | `sqlite:///./reconciliation.db`  | Any SQLAlchemy URL.                      |
| `PORT`         | `8000`                           | Used by the Docker/Render entrypoint.    |

**Postgres example:**
```bash
export DATABASE_URL="postgresql+psycopg://user:pass@host:5432/recon"
python seed.py sample_events.json
uvicorn app.main:app
```
(Uses the `psycopg` v3 driver, already in `requirements.txt`.)

---

## Assumptions & tradeoffs

**Assumptions**
- `event_id` is a globally unique, stable identifier — the basis for idempotency.
- Per the sample data, `amount`/`currency` are constant within a transaction and
  merchant names are stable, so materializing them on the transaction row is safe.
  We fill them from the first event that carries them.
- A `settled` event on a `failed` payment is a *discrepancy to report*, not an
  error to reject — the service records the truth and flags the inconsistency.
- Timestamps are business/event time; we also store `received_at` (ingest time).

**Tradeoffs**
- **Materialized state vs. compute-on-read.** I fold each event into a
  `transactions` row at ingest (O(1) per event) instead of recomputing status
  from the event log on every read. This makes reads and reconciliation cheap and
  index-friendly at the cost of a little write-time work and slight denormalization.
  For a read-heavy ops/reporting service this is the right trade.
- **Boolean flags vs. a state machine.** Flags make status derivation
  order-independent and idempotent-safe, and turn discrepancy rules into simple
  indexed boolean predicates. A strict state machine would reject illegal
  transitions — but for reconciliation we *want* to capture and report illegal
  states, not drop them.
- **SQLite default.** Chosen so a reviewer can run everything in two commands
  with no external services. The code is backend-agnostic (SQLAlchemy); flip
  `DATABASE_URL` to Postgres for concurrent production writes.
- **Offset pagination.** Simple and sufficient at this scale. For very deep
  pagination, keyset (cursor) pagination on `first_event_at` would scale better.
- **Batch ingest in one transaction.** A whole `POST /events` batch commits
  atomically; a validation error aborts the batch so callers never see a partial
  write. At very high volume I'd add a bulk `INSERT ... ON CONFLICT DO NOTHING`.

**What I'd do with more time**
- Alembic migrations instead of `create_all`.
- Keyset pagination and a `Cache-Control`/ETag on summary endpoints.
- `INSERT ... ON CONFLICT DO NOTHING` for true bulk idempotent ingest on Postgres.
- Structured logging + request IDs and a metrics endpoint.

---

## Testing

`tests/test_api.py` runs end-to-end against an isolated SQLite file and covers:
ingestion + fetch, **duplicate idempotency**, full lifecycle progression,
**out-of-order delivery**, batch ingest with an in-batch duplicate, filtering +
pagination, invalid-event rejection, and both major **discrepancy** classes plus
summary grouping.

```bash
pytest -q     # 10 passed
```

---

## AI tool disclosure

This solution was developed with the assistance of **Claude Code** (Anthropic).
AI was used to scaffold boilerplate, draft docs/tests, and iterate on the
implementation; all schema/index/idempotency design decisions and the final
code were reviewed and verified (tests pass, sample data reconciles exactly).
