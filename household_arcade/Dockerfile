ARG BUILD_ARCH
ARG BUILD_VERSION
FROM python:3.12-alpine

# tzdata: the app reads Home Assistant's time zone (e.g. "Europe/London") and
# needs the zoneinfo database to turn it into "today" (children's daily play
# time, quiet hours). python:alpine ships without one, so without this the app
# silently falls back to UTC.
RUN apk add --no-cache tzdata

WORKDIR /app

# Pure Python dependencies only: builds on every architecture without a compiler.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

LABEL \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="app" \
  io.hass.arch="${BUILD_ARCH}"

# /data is provided by the Supervisor automatically (always mapped, always
# writable): the SQLite database lives there, so scores survive updates.
# Ingress port only; nothing is published to the LAN.
EXPOSE 8104

# --no-proxy-headers: the ingress check needs the real TCP peer (Supervisor's proxy), never an address
# taken from X-Forwarded-For (tools/check_build.py checks it).
CMD ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8104", "--no-proxy-headers"]
