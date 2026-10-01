ARG BUILD_ARCH
ARG BUILD_VERSION
FROM python:3.12-alpine

# tzdata: python:alpine has no zoneinfo database; without it the app silently
# runs on UTC (quiet hours, reminders). The image libraries are Pillow's runtime
# needs (JPEG, PNG/zlib, WebP); the build packages are only used when pip has to
# compile Pillow (armv7 has no prebuilt musl wheel) and are removed afterwards.
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

# /data (database, photos) is always mapped by the Supervisor; /share by config.yaml.
EXPOSE 8103

# One worker on purpose: live updates (Server-Sent Events) are held in this process's memory.
CMD ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8103", "--workers", "1"]
