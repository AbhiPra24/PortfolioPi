FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y curl sqlite3 && rm -rf /var/lib/apt/lists/*

COPY dashboard/requirements-dashboard.txt .
RUN pip install --no-cache-dir -r requirements-dashboard.txt

COPY dashboard/ dashboard/

CMD ["streamlit", "run", "dashboard/app.py", "--server.port=8501", "--server.address=0.0.0.0"]
