# QUEST Rainfall: one container serving the FastAPI API and the built React app (same origin).

# ---------- stage 1: build the frontend ----------
FROM node:24-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- stage 2: runtime ----------
FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000 \
    QR_DATA_ROOT=/data/store \
    QR_STATIC_DIR=/app/static \
    QR_REFRESH_ENABLED=true \
    QR_LOG_LEVEL=INFO
WORKDIR /app

COPY backend/requirements.txt .
RUN pip install -r requirements.txt

# Build-time self-test: the bundled ecCodes native library must decode a real ECMWF GRIB2
# tp message (fails the build loudly if the wheel lacks a working libeccodes).
COPY backend/tests/fixtures/ifs_tp_20261115_00z_steps_0_6_12.grib2 backend/scripts/selftest_eccodes.py /tmp/
RUN python /tmp/selftest_eccodes.py /tmp/ifs_tp_20261115_00z_steps_0_6_12.grib2 && rm /tmp/selftest_eccodes.py /tmp/*.grib2

COPY backend/app ./app
COPY --from=web /web/dist ./static

RUN useradd --create-home --uid 1000 app && mkdir -p /data/store && chown -R app:app /data
USER app
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/api/health'%os.environ.get('PORT','8000'),timeout=4)"

# PORT is injected by most PaaS hosts (Render uses 10000); shell form so it expands.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*' --no-access-log"]
