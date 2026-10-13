ARG BUILD_ARCH
ARG BUILD_VERSION
# Pinned to one Alpine release so a rebuild can't quietly move to the next one, like the other household apps.
# (Pin by digest too — python:3.12-alpine3.22@sha256:… — when you can look it up; the Python version stays 3.12.)
FROM python:3.12-alpine3.22

# tzdata: Splitpot reads Home Assistant's time zone (weeks and months on the Dashboard, the ledger's order of
# picked days). python:alpine ships without a zoneinfo database, so without this every zone lookup fails and the
# app silently runs on UTC.
RUN apk add --no-cache tzdata

WORKDIR /app

# Plain uvicorn (no [standard] extras): Splitpot serves plain HTTP behind Home Assistant's ingress and needs none
# of uvloop, httptools, websockets or watchfiles; every package is pinned in requirements.txt.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

LABEL \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="app" \
  io.hass.arch="${BUILD_ARCH}"

# DATA_DIR and PORT are set by config.yaml's `environment:` block at runtime
# (DATA_DIR=/data, PORT=8099) to match Home Assistant's persistent storage
# and the configured ingress_port.
EXPOSE 8099

# --no-proxy-headers: the ingress check needs the real TCP peer (Supervisor's proxy), never an address
# taken from X-Forwarded-For (tools/check_build.py checks it).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8099} --no-proxy-headers"]
