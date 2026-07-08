# syntax=docker/dockerfile:1
FROM python:3.12-slim

RUN groupadd -g 1000 appuser && useradd -r -u 1000 -g appuser appuser

WORKDIR /app

RUN apt-get update && apt-get install -y sqlite3 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt

COPY . .
RUN chown -R appuser:appuser /app

USER appuser

CMD ["python", "main.py"]
