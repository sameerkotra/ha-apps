ARG BUILD_ARCH
ARG BUILD_VERSION
# The model server: Ollama's own image, pinned. Only its CPU parts are copied: the `ollama` binary, and the top level
# of /usr/lib/ollama — `llama-server` (the process that runs a model), its libraries and the CPU backends. Every GPU
# backend (CUDA, ROCm, Vulkan, MLX) is in a subfolder there and is left out: that is most of Ollama's image.
# Ollama is built against glibc, so this app uses Debian slim, not Alpine.
FROM ollama/ollama:0.40.0 AS ollama
RUN mkdir /ollama-cpu && find /usr/lib/ollama -maxdepth 1 ! -type d -exec cp -a {} /ollama-cpu/ \;

FROM python:3.13-slim-trixie

# tzdata: the daytime window follows Home Assistant's time zone. ca-certificates: downloads from Ollama's library.
# libstdc++6, libgomp1, libopenblas0: what llama-server and the CPU backends link against (Ollama's image has them).
RUN apt-get update && apt-get install -y --no-install-recommends tzdata ca-certificates libstdc++6 libgomp1 \
        libopenblas0 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ollama /usr/bin/ollama /usr/bin/ollama
COPY --from=ollama /ollama-cpu/ /usr/lib/ollama/

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

LABEL \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="app" \
  io.hass.arch="${BUILD_ARCH}"

# /data is mapped by the Supervisor: the database and /data/models. 8099: the admin page (ingress).
# 11434: the gateway for the other apps (Home Assistant's internal network; published only if the admin chooses).
EXPOSE 8099 11434

# --no-proxy-headers: the ingress check needs the real TCP peer (Supervisor's proxy), never an address taken from
# X-Forwarded-For (tools/check_build.py checks it). The gateway is started inside the app with proxy_headers=False.
CMD ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8099", "--no-proxy-headers"]
