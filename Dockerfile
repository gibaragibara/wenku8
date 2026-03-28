FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt /app/requirements.txt

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        p7zip-full \
        unzip \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r /app/requirements.txt \
    && pip install --no-cache-dir playwright \
    && python -m playwright install --with-deps chromium

COPY . /app

CMD ["python", "main.py", "playwright"]
