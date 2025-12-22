FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    procps \
    build-essential \
    python3-dev \
    gcc \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app


RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt /app/

RUN pip install --no-cache-dir -r requirements.txt

COPY procstat_textfile.py /app/procstat_textfile.py

ENV INTERVAL_SECONDS=5 \
    OUTPUT_DIR=/textfile \
    TOP_N=0 \
    MIN_RSS_BYTES=0 \
    MIN_CPU_PERCENT=0

CMD ["python", "-u", "/app/procstat_textfile.py"]