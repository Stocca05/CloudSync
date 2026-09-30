#!/usr/bin/env bash
# Apply a staged release to an existing native installation, preserving service secrets.
set -euo pipefail
if [ "$(id -u)" != 0 ] || [ ! -f /etc/cloudsync/role ]; then
  echo 'Richiede root e una installazione nativa esistente.' >&2; exit 1
fi
if [ -d /etc/pve ]; then echo 'Non eseguire sull’host Proxmox.' >&2; exit 1; fi
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [ "$source_dir" = /opt/cloudsync ]; then
  echo 'Estrai la nuova release in una directory di staging e avvia da quella directory.' >&2; exit 1
fi
role="$(cat /etc/cloudsync/role)"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir="/root/cloudsync-backups/$stamp"
umask 077
mkdir -p "$backup_dir"
cp -a /etc/cloudsync "$backup_dir/secrets"
tar -czf "$backup_dir/application.tar.gz" --exclude=.venv -C /opt cloudsync
if [ "$role" = server ]; then
  runuser -u postgres -- pg_dump -Fc cloudsync > "$backup_dir/database.dump"
fi
systemctl stop cloudsync-worker
if [ "$role" = server ]; then systemctl stop cloudsync-api; fi
rsync -a --exclude=.git --exclude=.venv --exclude=.env --exclude=runtime --exclude=artifacts "$source_dir/" /opt/cloudsync/
cd /opt/cloudsync
export UV_PYTHON_INSTALL_DIR=/opt/cloudsync-python
/opt/cloudsync-bootstrap/bin/uv sync --frozen --no-dev --python 3.12
cp deploy/cloudsync-api.service deploy/cloudsync-worker.service /etc/systemd/system/
systemctl daemon-reload
./start
printf 'Aggiornamento completato. Backup: %s\n' "$backup_dir"
if [ "${1:-}" = --retire-legacy ]; then
  # Only the explicitly identified predecessor, never unrelated containers.
  if command -v docker >/dev/null && docker inspect cloudsync --format '{{.Config.Image}}' 2>/dev/null | grep -qx 'cloudsync:latest'; then
    docker update --restart=no cloudsync >/dev/null
    docker stop cloudsync >/dev/null
    echo 'Vecchio container CloudSync fermato; dati e backup conservati.'
  fi
fi
