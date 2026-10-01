#!/bin/bash
set -e

# If custom command was passed (e.g. "python -m cloudsync.worker" or "uvicorn ..."), execute it directly
if [ "$#" -gt 0 ] && [ "$1" != "all-in-one" ]; then
    exec "$@"
fi

echo "============================================================"
echo "◈ CloudSync All-in-One Container Starting..."
echo "============================================================"

# Ensure persistent data directory exists
mkdir -p /data

# 1. Encryption Key (Fernet 32 url-safe base64-encoded bytes)
if [ -z "$ENCRYPTION_KEY" ]; then
    if [ -f /data/cloudsync.key ]; then
        ENCRYPTION_KEY=$(cat /data/cloudsync.key)
    else
        echo "--> Generating persistent encryption key in /data/cloudsync.key"
        ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
        echo -n "$ENCRYPTION_KEY" > /data/cloudsync.key
        chmod 600 /data/cloudsync.key
    fi
fi
export ENCRYPTION_KEY

# 2. Database URL (Default: SQLite on persistent /data volume)
if [ -z "$DATABASE_URL" ]; then
    export DATABASE_URL="sqlite:////data/cloudsync.db"
    echo "--> Using SQLite database at /data/cloudsync.db"
fi

# 3. Worker Authentication Token
if [ -z "$WORKER_TOKEN" ]; then
    if [ -f /data/worker.token ]; then
        WORKER_TOKEN=$(cat /data/worker.token)
    else
        echo "--> Generating persistent worker token in /data/worker.token"
        WORKER_TOKEN=$(python -c "import secrets; print(secrets.token_hex(24))")
        echo -n "$WORKER_TOKEN" > /data/worker.token
        chmod 600 /data/worker.token
    fi
fi
export WORKER_TOKEN

# 4. Initial Administrator Credentials
export ADMIN_USER="${ADMIN_USER:-admin}"
if [ -z "$ADMIN_PASSWORD" ]; then
    if [ -f /data/admin.password ]; then
        ADMIN_PASSWORD=$(cat /data/admin.password)
    else
        ADMIN_PASSWORD=$(python -c "import secrets; print(secrets.token_urlsafe(12))")
        echo -n "$ADMIN_PASSWORD" > /data/admin.password
        chmod 600 /data/admin.password
        echo ""
        echo "************************************************************"
        echo " ◈ CloudSync Credenziali Iniziali Amministratore:"
        echo "   Nome Utente: ${ADMIN_USER}"
        echo "   Password:    ${ADMIN_PASSWORD}"
        echo "   (Salvate in modo sicuro in /data/admin.password)"
        echo "************************************************************"
        echo ""
    fi
fi
export ADMIN_PASSWORD

export COOKIE_SECURE="${COOKIE_SECURE:-false}"
export ALLOW_REGISTRATION="${ALLOW_REGISTRATION:-true}"
export PORT="${PORT:-8000}"

# Internal worker configuration
export CONTROL_URL="http://127.0.0.1:${PORT}"
export NODE_NAME="local"

# Graceful shutdown handler
cleanup() {
    echo ""
    echo "◈ CloudSync shutting down gracefully..."
    if [ -n "$WORKER_PID" ]; then
        kill -TERM "$WORKER_PID" 2>/dev/null || true
    fi
    if [ -n "$API_PID" ]; then
        kill -TERM "$API_PID" 2>/dev/null || true
    fi
    wait 2>/dev/null || true
    echo "◈ CloudSync stopped."
    exit 0
}

trap cleanup SIGTERM SIGINT SIGHUP

echo "--> Starting CloudSync Control Plane API on 0.0.0.0:${PORT}..."
uvicorn cloudsync.api:create_app --factory --host 0.0.0.0 --port "${PORT}" --no-access-log &
API_PID=$!

# Wait for API to be healthy before starting worker
echo "--> Waiting for Control Plane API to become healthy..."
for i in $(seq 1 40); do
    if python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${PORT}/api/health', timeout=1)" 2>/dev/null; then
        echo "--> Control Plane API is healthy and operational!"
        break
    fi
    sleep 0.5
done

echo "--> Starting internal CloudSync Rclone Worker..."
python -m cloudsync.worker &
WORKER_PID=$!

echo "============================================================"
echo "◈ CloudSync All-in-One is ready at http://0.0.0.0:${PORT}"
echo "============================================================"

# Keep container alive and forward signals
wait "$API_PID"
