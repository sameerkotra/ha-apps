# Python minor version and Debian release pinned; rebuilds still pick up
# security patches within them.
FROM python:3.13-slim-trixie

# poppler-utils: PDF receipts become page images. fontconfig + a font: for
# PDFs that don't embed their own. tzdata: the daily check runs on Home
# Assistant's time zone (the Supervisor sets TZ).
RUN apt-get update && \
    apt-get install -y --no-install-recommends poppler-utils fontconfig fonts-dejavu-core tzdata && \
    rm -rf /var/lib/apt/lists/*

# pdfworker: the PDF tools run as this user (no home, no login), never as root, with resource limits
# (app/common/sandbox_run.py; tools/check_build.py checks the user is made).
RUN useradd --system --no-create-home --shell /usr/sbin/nologin pdfworker

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Everything the app keeps is in /data (the Supervisor provides it and backs it up).
ENV DATABASE_PATH=/data/receipt_price_intelligence.db \
    IMAGE_PATH=/data/receipts \
    HOST=0.0.0.0 \
    PORT=8099 \
    PYTHONUNBUFFERED=1

# Reached only through Home Assistant ingress (config.yaml has no ports:).
EXPOSE 8099

CMD ["python3", "-m", "app.main"]
