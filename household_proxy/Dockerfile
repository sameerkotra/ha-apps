ARG BUILD_ARCH
ARG BUILD_VERSION
# Pinned to one Alpine release so a rebuild cannot quietly move to the next one (add the digest when you can).
FROM python:3.12-alpine3.22

# tzdata: the app reads Home Assistant's time zone (times on the Monitor page and in alerts).
RUN apk add --no-cache tzdata

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

LABEL \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="app" \
  io.hass.arch="${BUILD_ARCH}"

# /data is provided by the Supervisor (always mapped, always writable): the SQLite database lives there.
EXPOSE 8099

# --no-proxy-headers: the ingress check needs the real TCP peer (Supervisor's proxy), never an address
# taken from X-Forwarded-For (tools/check_build.py checks it).
CMD ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8099", "--no-proxy-headers"]
