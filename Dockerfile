FROM rclone/rclone:1.75.1 AS rclone
FROM ghcr.io/astral-sh/uv:0.11.6 AS uv
FROM python:3.12-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
COPY --from=rclone /usr/local/bin/rclone /usr/local/bin/rclone
RUN groupadd -g 10001 cloudsync && useradd -u 10001 -g cloudsync -m cloudsync
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY cloudsync ./cloudsync
COPY frontend ./frontend
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "cloudsync.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
