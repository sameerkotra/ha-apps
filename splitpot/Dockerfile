ARG BUILD_ARCH
ARG BUILD_VERSION
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py ./
COPY public ./public

# DATA_DIR and PORT are set by config.yaml's `environment:` block at runtime
# (DATA_DIR=/data, PORT=8099) to match Home Assistant's persistent storage
# and the configured ingress_port.
EXPOSE 8099

LABEL \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="app" \
  io.hass.arch="${BUILD_ARCH}"

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8099}"]
