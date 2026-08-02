FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV GUARDIAN_ADMIN_TOKEN=demo
ENV GUARDIAN_DATA_DIR=/app/data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')"

# Render injects its own PORT (e.g. 10000); honor it instead of hardcoding.
CMD uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}
