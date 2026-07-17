# Solutions Engineer Take-Home Assignment

## Overview

You are building a lightweight backend service for a partner integrating with Setu.

The partner receives payment lifecycle events from multiple systems and needs a service to:

- ingest incoming events,
- maintain transaction and reconciliation state,
- expose APIs for operations teams,
- identify discrepancies between payment and settlement status.

Your task is to build and deploy this service.

## Problem Statement

Build a backend service using `Python` with either `Flask` or `FastAPI`, backed by any SQL database of your choice.

The system should support payment event ingestion, transaction retrieval, and reconciliation reporting in a way that is production-minded, deployable, and efficient.

## Functional Requirements

Implement the following APIs.

### 1. Ingest Events

`POST /events`

Accept payment lifecycle events such as:

- `payment_initiated`
- `payment_processed`
- `payment_failed`
- `settled`

Each event should contain enough information to associate it with a transaction and merchant.

Expected behavior:

- Event ingestion should be idempotent.
- Duplicate event submissions should not corrupt transaction state.
- Event history should be preserved.

### 2. List Transactions

`GET /transactions`

Support:

- filtering by `merchant_id`
- filtering by `status`
- filtering by date range
- pagination
- sorting

### 3. Fetch Transaction Details

`GET /transactions/{transaction_id}`

Return:

- transaction details
- current status
- related merchant information if relevant
- event history for that transaction

### 4. Reconciliation Summary

`GET /reconciliation/summary`

Return summaries grouped by dimensions such as:

- merchant
- date
- status

You may choose the exact request and response format as long as it is well documented.

### 5. Reconciliation Discrepancies

`GET /reconciliation/discrepancies`

Return transactions where payment state and settlement state are inconsistent.

Examples:

- payment marked processed but never settled
- settlement recorded for a failed payment
- duplicate events causing conflicting state transitions

## Data Model

Your schema should be able to support at least:

- merchants
- transactions
- payment events or event history
- settlement or reconciliation status

You are free to choose the exact schema design, but it should be easy to reason about and efficient to query.

## Sample Data

A `sample_events.json` file is provided with ~10,000 events across 5 merchants covering realistic scenarios (successful flows, failures, pending settlements, discrepancies, and duplicate events). 

Here is what a single event looks like:

```json
{
  "event_id": "b768e3a7-9eb3-4603-b21c-a54cc95661bc",
  "event_type": "payment_initiated",
  "transaction_id": "2f86e94c-239c-4302-9874-75f28e3474ee",
  "merchant_id": "merchant_2",
  "merchant_name": "FreshBasket",
  "amount": 15248.29,
  "currency": "INR",
  "timestamp": "2026-01-08T12:11:58.085567+00:00"
}
```

You are also free to generate your own sample data or modify the provided file. There are no constraints on the data format or spec — just ensure your submission includes enough volume and variety to demonstrate your query design and handling of edge cases.

Whichever approach you take, document it in your `README`.

At a minimum, your data should include:

- 3 or more merchants
- 10,000 or more event records
- a realistic mix of:
  - successful transactions
  - failed transactions
  - duplicate events
  - inconsistent or unreconciled records

This is important because we want to evaluate query design and practical performance awareness.

## Deployment Requirement

Your submission must include a working public deployment.

You may deploy on any platform of your choice, including AWS, GCP, Render, Railway, Fly.io, or similar services. There is no restriction on hosting provider.

The demo must be functional. Placeholder deployments or non-working URLs will be treated as incomplete submissions.

If you are unable to deploy or unfamiliar with deployments, ensure your local setup is well documented so the reviewer can run and test the service easily.

## Submission Requirements

Please submit:

- a public Git repository (`GitHub`, `GitLab`, or equivalent)
- a working postman Collection
- a screen recording of the demo - walkthrough of all the APIs (YouTube, Loom, Google Drive, or any shareable link)
- a `README` with:
  - architecture overview
  - setup instructions for local development
  - API documentation
  - deployment details
  - assumptions and tradeoffs

## Constraints

- Time limit: `3 days` from when the assignment is shared with you
- If you need additional time, please write to the hiring team before the deadline
- You may use internet resources
- If you use AI tools, please disclose which tools you used and how

## What We Will Evaluate

We will primarily evaluate:

- whether the demo works end-to-end
- Python code quality and engineering judgment
- SQL schema design and query quality
- handling of duplicate events and idempotency
- API design, validation, and error handling
- clarity of documentation and reproducibility

## Notes for Candidates

Here is what a strong submission typically looks like. Use this as a guide while building.

**Your deployment or local setup is the first thing we check.** If the reviewer cannot run your service within a few minutes, nothing else matters. A working demo on any platform (or a clean local setup with clear instructions) carries the most weight.

**SQL design matters more than Python cleverness.** We will look at your schema, indexes, and queries closely. Aggregations, filtering, and pagination should happen in SQL — not in Python loops. Think about what queries your APIs will run and design your schema to support them efficiently.

**Idempotency is not optional.** Submitting the same event twice should not create duplicate records or corrupt transaction state. Show us how you handle this — whether through unique constraints, event ID checks, or another mechanism.

**Show us you tested your own work.** This can be automated tests, a Postman collection, a screen recording, or all three. We want to see evidence that you exercised your endpoints and thought about edge cases.

**Document your decisions, not just your setup.** We care about *why* you made certain choices — schema design, indexes, framework choice, what you simplified, what you'd do differently with more time. A short tradeoffs section in your README goes a long way.

**Keep it simple.** We are not looking for unnecessary complexity. A practical, well-structured solution that works and is easy to review reflects stronger engineering judgment than an over-engineered one that doesn't.