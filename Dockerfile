# ==============================================================================
# CloudSync - Optimized Multi-Stage Dockerfile
# Combines official Rclone binary, Cloudflare Tunnel, and Python 3.12 + FastAPI
# in a single container.
# ==============================================================================

# Stage 1: Official Rclone binary extraction
FROM rclone/rclone:latest AS rclone-bin

# Stage 2: Official Cloudflare Tunnel binary extraction
FROM cloudflare/cloudflared:latest AS cloudflared-bin

# Stage 3: Final Production Runtime (Python 3.12)
FROM python:3.12-slim-bookworm AS runtime

LABEL maintainer="CloudSync DevOps Architect"
LABEL description="Secure, in-RAM private file mover between Google Drive and iCloud Drive"

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    RCLONE_CONFIG=/app/config/rclone.conf \
    RCLONE_LOG_LEVEL=INFO \
    RCLONE_DRIVE_EXPORT_FORMATS=docx,xlsx,pptx,pdf \
    ENABLE_TUNNEL=true

# Install minimal OS dependencies: curl for healthcheck & rclone communication, ca-certificates for TLS
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Copy official Rclone binary from Stage 1
COPY --from=rclone-bin /usr/local/bin/rclone /usr/local/bin/rclone

# Copy official Cloudflare Tunnel binary from Stage 2
COPY --from=cloudflared-bin /usr/local/bin/cloudflared /usr/local/bin/cloudflared

# Install 'uv' for ultra-fast, reproducible dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Set working directory
WORKDIR /app

# Copy project specifications and install Python dependencies
COPY pyproject.toml README.md ./
RUN uv pip install --system --no-cache .

# Copy application source code and frontend
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY config/rclone.conf.example ./config/rclone.conf.example
COPY entrypoint.sh ./entrypoint.sh

# Create persistent directories with appropriate permissions
RUN mkdir -p /app/config /app/data && \
    chmod +x /app/entrypoint.sh

# Expose Web UI (8000) and Rclone OAuth redirect receiver (53682)
EXPOSE 8000 53682

# Container healthcheck
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# Execute startup entrypoint
ENTRYPOINT ["/app/entrypoint.sh"]
