# syntax=docker/dockerfile:1
FROM python:3.12-slim

RUN groupadd -g 1000 appuser && useradd -r -u 1000 -g appuser appuser

WORKDIR /app

RUN apt-get update && apt-get install -y curl sqlite3 && rm -rf /var/lib/apt/lists/*

COPY dashboard/requirements-dashboard.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements-dashboard.txt

COPY dashboard/ dashboard/
RUN chown -R appuser:appuser /app

USER appuser

CMD ["streamlit", "run", "dashboard/app.py", "--server.port=8501", "--server.address=0.0.0.0"]
