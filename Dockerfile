FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ECOMEVO_DATA=/app/outputs/runtime

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 ecomevo \
    && useradd --system --uid 10001 --gid ecomevo --home-dir /nonexistent --shell /usr/sbin/nologin ecomevo

COPY pyproject.toml README.md ./
COPY ecomevo ./ecomevo
COPY frontend ./frontend

RUN python -m pip install --no-cache-dir --upgrade pip==26.2.1 \
    && python -m pip install --no-cache-dir . \
    && mkdir -p /app/outputs/runtime \
    && chown -R 10001:10001 /app/outputs

USER 10001:10001

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).read()"]

CMD ["uvicorn", "ecomevo.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
