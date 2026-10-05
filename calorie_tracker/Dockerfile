ARG BUILD_ARCH
ARG BUILD_VERSION
FROM python:3.12-alpine

# tzdata: the app reads Home Assistant's time zone (e.g. "Europe/Berlin") and
# needs the zoneinfo database to turn it into "today". python:alpine ships
# without one, so without this every ZoneInfo() lookup fails and the app
# silently runs on UTC (the day rolls over at UTC midnight).
RUN apk add --no-cache tzdata

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

LABEL \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="app" \
  io.hass.arch="${BUILD_ARCH}"

# /data is provided by the Supervisor automatically (always mapped, always
# writable) — this is where the SQLite database lives, so data survives
# app updates/rebuilds.
EXPOSE 8099

# --no-proxy-headers: the ingress check needs the real TCP peer (Supervisor's proxy), never an address
# taken from X-Forwarded-For (tools/check_build.py checks it).
CMD ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8099", "--no-proxy-headers"]
