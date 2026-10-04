FROM python:3.12-slim

# Unbuffered so container logs appear in real time; no .pyc in an ephemeral FS.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MACRO_TRACKER_DB=/data/macro_tracker.db

WORKDIR /app

# Dependencies first, so a source-only change does not reinstall them.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY static/ static/
COPY scripts/ scripts/

# SQLite lives on a volume: on a container's own filesystem every log would be
# lost on redeploy.
RUN mkdir -p /data && useradd --create-home --uid 10001 app && chown -R app /data /app
VOLUME /data
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')"

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
