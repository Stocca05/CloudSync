#!/usr/bin/env bash
# ==============================================================================
# CloudSync - Quick Launcher & Verification Script
# Starts CloudSync via Docker Compose and prints the temporary Cloudflare Tunnel link.
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "================================================================================"
echo " ☁️  CloudSync - Avvio Progetto con Link Cloudflare Temporaneo"
echo "================================================================================"

mkdir -p "$SCRIPT_DIR/config"
mkdir -p "$SCRIPT_DIR/data"
rm -f "$SCRIPT_DIR/data/tunnel_url.txt"

# If rclone.conf doesn't exist, initialize from example
if [ ! -f "$SCRIPT_DIR/config/rclone.conf" ]; then
    if [ -f "$SCRIPT_DIR/config/rclone.conf.example" ]; then
        cp "$SCRIPT_DIR/config/rclone.conf.example" "$SCRIPT_DIR/config/rclone.conf"
    else
        touch "$SCRIPT_DIR/config/rclone.conf"
    fi
fi

# Detect execution mode (Docker or Native)
USE_DOCKER=true
if [ "$1" = "--local" ] || ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
    USE_DOCKER=false
fi

if [ "$USE_DOCKER" = "true" ]; then
    echo "🐳 Avvio container Docker tramite docker-compose..."
    if command -v docker-compose >/dev/null 2>&1; then
        docker-compose up -d --build
    else
        docker compose up -d --build
    fi

    echo "⏳ Attesa avvio container ed estrazione link Cloudflare..."
    MAX_WAIT=25
    WAIT_COUNT=0
    TUNNEL_URL=""

    while [ $WAIT_COUNT -lt $MAX_WAIT ]; do
        if [ -f "$SCRIPT_DIR/data/tunnel_url.txt" ]; then
            TUNNEL_URL=$(cat "$SCRIPT_DIR/data/tunnel_url.txt" | tr -d '\r\n')
            if [ -n "$TUNNEL_URL" ]; then
                break
            fi
        fi
        # Check logs directly as fallback
        LOG_URL=$(docker logs cloudsync 2>&1 | grep -oE "https://[a-zA-Z0-9-]+\.trycloudflare\.com" | tail -n 1 || true)
        if [ -n "$LOG_URL" ]; then
            TUNNEL_URL="$LOG_URL"
            echo "$LOG_URL" > "$SCRIPT_DIR/data/tunnel_url.txt"
            break
        fi
        sleep 1
        WAIT_COUNT=$((WAIT_COUNT + 1))
    done

else
    echo "🖥️  Avvio in modalità locale (Host)..."
    # Start rclone daemon
    rclone rcd --rc-addr=127.0.0.1:5572 --rc-no-auth --config="$SCRIPT_DIR/config/rclone.conf" &
    RCLONE_PID=$!

    # Start FastAPI
    uv run uvicorn backend.main:app --host 0.0.0.0 --port 8000 &
    UVICORN_PID=$!

    # Start Cloudflare Tunnel
    cloudflared tunnel --url http://127.0.0.1:8000 --no-autoupdate > "$SCRIPT_DIR/data/tunnel.log" 2>&1 &
    CF_PID=$!

    for i in {1..20}; do
        TUNNEL_URL=$(grep -oE "https://[a-zA-Z0-9-]+\.trycloudflare\.com" "$SCRIPT_DIR/data/tunnel.log" 2>/dev/null | tail -n 1 || true)
        if [ -n "$TUNNEL_URL" ]; then
            echo "$TUNNEL_URL" > "$SCRIPT_DIR/data/tunnel_url.txt"
            break
        fi
        sleep 1
    done
fi

echo ""
echo "================================================================================"
echo " 🎉 CLOUDSYNC È ATTIVO E FUNZIONANTE!"
echo "================================================================================"
echo " 🏠 Accesso Locale:    http://localhost:8000"
if [ -n "$TUNNEL_URL" ]; then
    echo " 🌐 Link Cloudflare:   $TUNNEL_URL"
    echo "    (Accessibile pubblicamente da qualsiasi dispositivo/browser tramite HTTPS)"
else
    echo " ℹ️  Link Cloudflare in fase di generazione. Controlla la Web UI o ./data/tunnel_url.txt"
fi
echo "================================================================================"
echo " 💡 Per visualizzare i log del container: docker logs -f cloudsync"
echo " 🛑 Per arrestare: docker-compose down"
echo "================================================================================"

# Automatically open browser if desktop environment is available
TARGET_URL="${TUNNEL_URL:-http://localhost:8000}"
if command -v xdg-open >/dev/null 2>&1 && [ -n "$DISPLAY$WAYLAND_DISPLAY" ]; then
    xdg-open "$TARGET_URL" >/dev/null 2>&1 &
elif command -v open >/dev/null 2>&1; then
    open "$TARGET_URL" >/dev/null 2>&1 &
fi
