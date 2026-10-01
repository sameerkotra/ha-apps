ARG BUILD_ARCH
ARG BUILD_VERSION
FROM python:3.12-alpine

# tzdata: the app reads Home Assistant's time zone (e.g. "Europe/London") and
# needs the zoneinfo database to turn it into "today". python:alpine ships
# without one, so without this the app silently falls back to UTC.
# Pillow's runtime libraries (JPEG, PNG/zlib, WebP) for maintenance photo
# previews; the build packages are only used when pip has to compile Pillow
# (armv7 has no prebuilt musl wheel) and are removed afterwards.
RUN apk add --no-cache tzdata libjpeg-turbo zlib libwebp lcms2

WORKDIR /app

COPY requirements.txt .
RUN apk add --no-cache --virtual .build-deps build-base jpeg-dev zlib-dev libwebp-dev lcms2-dev \
    && pip install --no-cache-dir -r requirements.txt \
    && apk del .build-deps

COPY app ./app

LABEL \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="app" \
  io.hass.arch="${BUILD_ARCH}"

# /data is provided by the Supervisor automatically (always mapped, always
# writable) — this is where the SQLite database lives, so data survives
# app updates/rebuilds. Ingress port only; nothing is published to the LAN.
EXPOSE 8100

CMD ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
