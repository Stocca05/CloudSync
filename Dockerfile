FROM rclone/rclone:1.75.1 AS rclone
FROM ghcr.io/astral-sh/uv:0.11.6 AS uv
FROM python:3.12-slim-bookworm

COPY --from=uv /uv /usr/local/bin/uv
COPY --from=rclone /usr/local/bin/rclone /usr/local/bin/rclone

# Create non-root cloudsync user and persistent data volume
RUN groupadd -g 10001 cloudsync && \
    useradd -u 10001 -g cloudsync -m -s /bin/bash cloudsync && \
    mkdir -p /data && \
    chown -R 10001:10001 /data && \
    chmod 777 /data

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY cloudsync ./cloudsync
COPY frontend ./frontend
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

VOLUME ["/data"]
USER 10001:10001
EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
