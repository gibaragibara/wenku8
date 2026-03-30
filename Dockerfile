FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

ARG RCLONE_VERSION=1.73.3

COPY requirements.txt /app/requirements.txt

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        p7zip-full \
        unzip \
    && arch="$(dpkg --print-architecture)" \
    && case "$arch" in \
        amd64) rclone_arch='amd64' ;; \
        arm64) rclone_arch='arm64' ;; \
        armhf) rclone_arch='arm-v7' ;; \
        *) echo "Unsupported architecture for rclone: $arch" >&2; exit 1 ;; \
      esac \
    && curl -fsSL "https://downloads.rclone.org/v${RCLONE_VERSION}/rclone-v${RCLONE_VERSION}-linux-${rclone_arch}.zip" -o /tmp/rclone.zip \
    && unzip -q /tmp/rclone.zip -d /tmp \
    && install -m 0755 "/tmp/rclone-v${RCLONE_VERSION}-linux-${rclone_arch}/rclone" /usr/local/bin/rclone \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /tmp/rclone.zip "/tmp/rclone-v${RCLONE_VERSION}-linux-${rclone_arch}" \
    && pip install --no-cache-dir -r /app/requirements.txt \
    && pip install --no-cache-dir playwright \
    && python -m playwright install --with-deps chromium

COPY . /app

CMD ["python", "main.py", "playwright"]
