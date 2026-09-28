#!/usr/bin/env bash
# ==============================================================================
# CloudSync Container Entrypoint Script
# Starts Rclone Daemon, FastAPI backend, and Cloudflare Quick Tunnel with
# graceful shutdown and health monitoring.
# ==============================================================================
set -e

RCLONE_CONFIG_FILE="${RCLONE_CONFIG:-/app/config/rclone.conf}"
RCLONE_ADDR="${RCLONE_RC_ADDR:-127.0.0.1:5572}"
RCLONE_LOG_LEVEL="${RCLONE_LOG_LEVEL:-INFO}"
ENABLE_TUNNEL="${ENABLE_TUNNEL:-true}"

echo "================================================================================"
echo " CloudSync Starting (Docker Containerized Orchestrator)"
echo " Privacy: 100% In-RAM Piping between Google Drive & iCloud Drive"
echo "================================================================================"

# Verify or initialize config directory
mkdir -p "$(dirname "$RCLONE_CONFIG_FILE")"
mkdir -p /app/data

# Clean old tunnel URL file
rm -f /app/data/tunnel_url.txt

if [ ! -f "$RCLONE_CONFIG_FILE" ]; then
    echo "⚠️  ATTENZIONE: File di configurazione '$RCLONE_CONFIG_FILE' non trovato."
    echo "ℹ️  Inizializzazione file di configurazione rclone.conf..."
    touch "$RCLONE_CONFIG_FILE"
    echo "👉 Configura i remoti 'gdrive' e 'icloud' montando il tuo rclone.conf in ./config/"
fi

# ------------------------------------------------------------------------------
# 1. Start Rclone Remote Control Daemon
# ------------------------------------------------------------------------------
echo "🚀 Avvio Rclone Daemon su $RCLONE_ADDR..."

# Prevent duplicate binding from environment variable
unset RCLONE_RC_ADDR

# Launch rclone rcd in the background
rclone rcd \
    --rc-addr="$RCLONE_ADDR" \
    --rc-no-auth \
    --config="$RCLONE_CONFIG_FILE" \
    --log-level="$RCLONE_LOG_LEVEL" \
    --drive-export-formats="${RCLONE_DRIVE_EXPORT_FORMATS:-docx,xlsx,pptx,pdf}" &

RCLONE_PID=$!

# Trap signals for graceful shutdown
cleanup() {
    echo "🛑 Segnale di arresto ricevuto. Terminazione processi in corso..."
    [ -n "$TUNNEL_PID" ] && kill -TERM "$TUNNEL_PID" 2>/dev/null || true
    [ -n "$UVICORN_PID" ] && kill -TERM "$UVICORN_PID" 2>/dev/null || true
    kill -TERM "$RCLONE_PID" 2>/dev/null || true
    wait "$RCLONE_PID" 2>/dev/null || true
    echo "✅ Shutdown completato."
    exit 0
}
trap cleanup SIGTERM SIGINT

# ------------------------------------------------------------------------------
# 2. Health check: Wait for Rclone RC to become responsive
# ------------------------------------------------------------------------------
echo "⏳ Attesa disponibilità API Rclone RC..."
MAX_RETRIES=30
RETRY_COUNT=0
until curl -s -f -X POST "http://$RCLONE_ADDR/rc/noop" >/dev/null 2>&1; do
    RETRY_COUNT=$((RETRY_COUNT + 1))
    if [ $RETRY_COUNT -ge $MAX_RETRIES ]; then
        echo "❌ Timeout durante l'attesa del demone Rclone. Uscita."
        kill -9 "$RCLONE_PID" 2>/dev/null || true
        exit 1
    fi
    sleep 0.5
done

echo "✅ Demone Rclone attivo e responsivo (PID: $RCLONE_PID)."

# ------------------------------------------------------------------------------
# 3. Start Backend FastAPI via Uvicorn
# ------------------------------------------------------------------------------
echo "🌐 Avvio server FastAPI su porta 8000..."
uvicorn backend.main:app --host 0.0.0.0 --port 8000 &
UVICORN_PID=$!

# Wait for FastAPI to respond locally
until curl -s -f "http://127.0.0.1:8000/api/health" >/dev/null 2>&1; do
    sleep 0.5
done

# ------------------------------------------------------------------------------
# 4. Optional: Start Cloudflare Quick Tunnel (Public Temporary HTTPS Access)
# ------------------------------------------------------------------------------
if [ "$ENABLE_TUNNEL" = "true" ] && command -v cloudflared >/dev/null 2>&1; then
    echo "☁️  Inizializzazione Tunnel Cloudflare (trycloudflare.com)..."
    cloudflared tunnel --url http://127.0.0.1:8000 --no-autoupdate > /app/data/tunnel.log 2>&1 &
    TUNNEL_PID=$!

    # Asynchronously extract and log public URL
    (
        for i in {1..20}; do
            URL=$(grep -oE "https://[a-zA-Z0-9-]+\.trycloudflare\.com" /app/data/tunnel.log 2>/dev/null || true)
            if [ -n "$URL" ]; then
                echo "$URL" > /app/data/tunnel_url.txt
                echo "================================================================================"
                echo " 🌐 LINK TEMPORANEO CLOUDFLARE ATTIVO:"
                echo " 👉 $URL"
                echo "================================================================================"
                break
            fi
            sleep 1
        done
    ) &
fi

# Wait for uvicorn or rclone to exit
wait -n "$RCLONE_PID" "$UVICORN_PID"
