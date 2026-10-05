ARG BUILD_ARCH
ARG BUILD_VERSION
FROM python:3.12-alpine

# tzdata for local times in logs; the build packages are only needed where pip has to
# compile argon2-cffi or cryptography (armv7) and are removed again afterwards.
RUN apk add --no-cache tzdata libffi

WORKDIR /app

COPY requirements.txt .
RUN apk add --no-cache --virtual .build-deps build-base libffi-dev openssl-dev cargo \
    && pip install --no-cache-dir -r requirements.txt \
    && apk del .build-deps

COPY app ./app

LABEL \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="app" \
  io.hass.arch="${BUILD_ARCH}"

# /data (database and encrypted vault files) is always mapped by the Supervisor.
EXPOSE 8101

# --no-proxy-headers: the ingress check needs the real TCP peer (Supervisor's proxy), never an address
# taken from X-Forwarded-For (tools/check_build.py checks it).
CMD ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8101", "--no-proxy-headers"]
