ARG BUILD_ARCH
ARG BUILD_VERSION
FROM python:3.12-alpine

# tzdata: python:alpine has no zoneinfo database; without it the app silently
# runs on UTC instead of Home Assistant's time zone.
RUN apk add --no-cache tzdata

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

LABEL \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="app" \
  io.hass.arch="${BUILD_ARCH}"

# /data (the database) is always mapped by the Supervisor; /share by config.yaml.
# Ingress port only; nothing is published to the LAN.
EXPOSE 8105

# --no-proxy-headers: the ingress check needs the real TCP peer (Supervisor's proxy), never an address
# taken from X-Forwarded-For (tools/check_build.py checks it).
CMD ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8105", "--no-proxy-headers"]
