ARG BUILD_ARCH
ARG BUILD_VERSION
FROM python:3.12-alpine

# tzdata: python:alpine has no zoneinfo database; without it the app silently
# runs on UTC. The image libraries are Pillow's runtime needs (JPEG, PNG/zlib,
# WebP); the build packages are only used when pip has to compile Pillow
# (armv7 has no prebuilt musl wheel) and are removed again afterwards.
RUN apk add --no-cache tzdata libjpeg-turbo zlib libwebp

WORKDIR /app

COPY requirements.txt .
RUN apk add --no-cache --virtual .build-deps build-base jpeg-dev zlib-dev libwebp-dev \
    && pip install --no-cache-dir -r requirements.txt \
    && apk del .build-deps

COPY app ./app

LABEL \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="app" \
  io.hass.arch="${BUILD_ARCH}"

# /data (database) is always mapped by the Supervisor; /share (media) is mapped
# by config.yaml. Ingress port only; nothing is published to the LAN.
EXPOSE 8102

# --no-proxy-headers: the ingress check needs the real TCP peer (Supervisor's proxy), never an address
# taken from X-Forwarded-For (tools/check_build.py checks it).
CMD ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8102", "--no-proxy-headers"]
