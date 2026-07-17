FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY seed.py sample_events.json ./

EXPOSE 8000

# Seed the DB on boot if empty, then serve. For SQLite this makes the container
# self-contained; for Postgres, point DATABASE_URL at the managed instance and
# run `python seed.py` once as a one-off job instead.
CMD ["sh", "-c", "python seed.py sample_events.json || true; uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
