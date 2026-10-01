# Python minor version and Debian release pinned; rebuilds still pick up
# security patches within them.
FROM python:3.13-slim-trixie

# poppler-utils: pdftotext / pdftoppm (text extraction, page images).
# tzdata: the slim image has no zoneinfo, so the TZ that bootstrap.py sets
# would otherwise silently stay UTC.
RUN apt-get update && \
    apt-get install -y --no-install-recommends poppler-utils tzdata && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Reached only through Home Assistant ingress (config.yaml has no ports:).
# The database and uploaded PDFs live in /data, which Supervisor provides.
EXPOSE 8321

CMD ["python3", "bootstrap.py"]
