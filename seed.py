"""Load sample_events.json into the database via the ingest pipeline.

Usage:
    python seed.py [path/to/sample_events.json]

Uses the same idempotent ingest path as the API, so re-running is safe and
duplicate events in the file are reported, not double-counted.
"""
import json
import sys
import time

from app.db import Base, SessionLocal, engine
from app.ingest import ingest_events


def main(path: str = "sample_events.json", batch_size: int = 1000) -> None:
    Base.metadata.create_all(bind=engine)
    with open(path) as f:
        events = json.load(f)

    print(f"Loaded {len(events)} events from {path}")
    started = time.time()
    total_accepted = total_dupes = 0

    db = SessionLocal()
    try:
        for i in range(0, len(events), batch_size):
            batch = events[i : i + batch_size]
            result = ingest_events(db, batch)
            total_accepted += result["accepted"]
            total_dupes += result["duplicates"]
    finally:
        db.close()

    elapsed = time.time() - started
    print(f"Accepted: {total_accepted}  Duplicates skipped: {total_dupes}")
    print(f"Done in {elapsed:.2f}s")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "sample_events.json")
