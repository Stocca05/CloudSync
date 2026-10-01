#!/usr/bin/env bash
# Script di avvio per il Nodo Mac di CloudSync con Dashboard Terminale

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Controlla Python 3
if ! command -v python3 &> /dev/null; then
    echo "Errore: python3 non trovato!"
    exit 1
fi

# Controlla rclone
if ! command -v rclone &> /dev/null; then
    echo "Errore: rclone non trovato nel PATH!"
    exit 1
fi

# Avvia la dashboard
exec python3 "$SCRIPT_DIR/node_dashboard.py"
