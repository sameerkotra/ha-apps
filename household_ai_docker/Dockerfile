# Household AI on its own Docker host: Ollama behind the household apps' gateway (port 11434) and a settings page
# (port 8080, no login). Build and start it with `docker compose up -d --build` (README.md).
#
# OLLAMA_LIBS: "all" (the default) copies all of Ollama's libraries, so an NVIDIA graphics card is used when the
# container is given one (docker-compose.yml's `gpus`); "cpu" copies only the CPU parts — a much smaller image for a
# machine without a supported graphics card.
ARG OLLAMA_VERSION=0.40.0
FROM docker.io/ollama/ollama:${OLLAMA_VERSION} AS ollama
ARG OLLAMA_LIBS=all
RUN mkdir /ollama-libs \
    && if [ "$OLLAMA_LIBS" = "cpu" ]; then \
         find /usr/lib/ollama -maxdepth 1 ! -type d -exec cp -a {} /ollama-libs/ \; ; \
       else \
         cp -a /usr/lib/ollama/. /ollama-libs/ ; \
       fi

FROM docker.io/library/python:3.13-slim-trixie

# tzdata: the daytime window follows TZ. ca-certificates: model downloads. libstdc++6, libgomp1, libopenblas0: what
# llama-server and the CPU backends link against.
RUN apt-get update && apt-get install -y --no-install-recommends tzdata ca-certificates libstdc++6 libgomp1 \
        libopenblas0 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ollama /usr/bin/ollama /usr/bin/ollama
COPY --from=ollama /ollama-libs/ /usr/lib/ollama/

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV DATA_DIR=/data \
    WEB_PORT=8080 \
    GATEWAY_PORT=11434 \
    PUBLIC_GATEWAY_PORT=11434 \
    TZ=UTC \
    PYTHONUNBUFFERED=1
VOLUME /data

# 8080: the settings page. 11434: the gateway the household apps use (what an Ollama would answer on).
EXPOSE 8080 11434

HEALTHCHECK --interval=60s --timeout=10s --start-period=60s --retries=3 \
  CMD python3 -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:11434/api/version', timeout=5)" || exit 1

# --no-proxy-headers: callers are told apart by their real address, never one taken from X-Forwarded-For.
CMD ["sh", "-c", "exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port ${WEB_PORT} --no-proxy-headers"]
