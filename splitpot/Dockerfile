ARG BUILD_ARCH
ARG BUILD_VERSION
FROM python:3.12-slim

WORKDIR /app

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
