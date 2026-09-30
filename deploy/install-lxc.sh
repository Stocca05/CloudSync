#!/usr/bin/env bash
# Run only inside a dedicated Debian/Ubuntu LXC or VM, never on the Proxmox host.
set -euo pipefail
if [ "$(id -u)" != 0 ]; then echo 'Esegui come root nel container dedicato.' >&2; exit 1; fi
if [ -d /etc/pve ]; then echo 'Questo è l’host Proxmox: usa un container/VM dedicato.' >&2; exit 1; fi
command -v systemctl >/dev/null || { echo 'È richiesto systemd.' >&2; exit 1; }
role="${1:-server}"
case "$role" in server|worker) ;; *) echo 'Ruolo non valido' >&2; exit 1;; esac
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -f /etc/cloudsync/role ]; then
  echo 'Installazione già presente. Usa /opt/cloudsync/start.'
  exit 0
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl unzip python3-venv openssl rsync
if [ "$role" = server ]; then apt-get install -y postgresql; fi
id cloudsync >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/cloudsync --shell /usr/sbin/nologin cloudsync
install -d -m 750 -o root -g cloudsync /etc/cloudsync
install -d -m 755 /opt/cloudsync
rsync -a --exclude=.git --exclude=.venv --exclude=.env --exclude=runtime --exclude=artifacts "$source_dir/" /opt/cloudsync/
python3 -m venv /opt/cloudsync-bootstrap
/opt/cloudsync-bootstrap/bin/pip install 'uv==0.11.6'
UV=/opt/cloudsync-bootstrap/bin/uv
export UV_PYTHON_INSTALL_DIR=/opt/cloudsync-python
"$UV" python install 3.12
cd /opt/cloudsync
"$UV" sync --frozen --no-dev --python 3.12
case "$(uname -m)" in x86_64) arch=amd64;; aarch64|arm64) arch=arm64;; *) echo 'Architettura non supportata' >&2; exit 1;; esac
rclone_version=v1.75.1
archive="rclone-${rclone_version}-linux-${arch}.zip"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT
curl --fail --location --retry 3 "https://downloads.rclone.org/${rclone_version}/${archive}" -o "$tmp_dir/$archive"
curl --fail --location --retry 3 "https://downloads.rclone.org/${rclone_version}/SHA256SUMS" -o "$tmp_dir/SHA256SUMS"
(cd "$tmp_dir"; awk -v file="$archive" '$2 == file {print}' SHA256SUMS > selected.sha256; test -s selected.sha256; sha256sum -c selected.sha256)
unzip -q "$tmp_dir/$archive" -d "$tmp_dir"
install -m 755 "$tmp_dir/rclone-${rclone_version}-linux-${arch}/rclone" /usr/local/bin/rclone
umask 077
if [ "$role" = server ]; then
  db_secret="$(openssl rand -hex 32)"
  admin_secret="$(openssl rand -hex 10)"
  worker_secret="$(openssl rand -hex 48)"
  encryption_key="$(openssl rand -base64 32 | tr '/+' '_-')"
  systemctl enable --now postgresql
  if runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='cloudsync'" | grep -q 1; then
    echo 'Ruolo PostgreSQL cloudsync già esistente: interrompo per non modificare dati preesistenti.' >&2; exit 1
  fi
  runuser -u postgres -- psql -v ON_ERROR_STOP=1 -c "CREATE ROLE cloudsync LOGIN PASSWORD '$db_secret'"
  runuser -u postgres -- createdb --owner=cloudsync --encoding=UTF8 --template=template0 --locale=C.UTF-8 cloudsync
  cat > /etc/cloudsync/server.env <<ENV
DATABASE_URL=postgresql+psycopg://cloudsync:$db_secret@127.0.0.1/cloudsync
ENCRYPTION_KEY=$encryption_key
ADMIN_USER=admin
ADMIN_PASSWORD=$admin_secret
WORKER_TOKEN=$worker_secret
COOKIE_SECURE=false
ALLOW_REGISTRATION=true
GLOBAL_BPS=52428800
ENV
  cat > /etc/cloudsync/worker.env <<ENV
CONTROL_URL=http://127.0.0.1:8080
WORKER_TOKEN=$worker_secret
WORKER_SLOTS=2
SFTP_KNOWN_HOSTS=/etc/cloudsync/known_hosts
ENV
else
  if [ ! -f "$source_dir/.env" ]; then echo 'Prepara .env con CONTROL_URL e WORKER_TOKEN prima di installare il worker.' >&2; exit 1; fi
  cp "$source_dir/.env" /etc/cloudsync/worker.env
fi
touch /etc/cloudsync/known_hosts
chmod 644 /etc/cloudsync/known_hosts
cp deploy/cloudsync-api.service deploy/cloudsync-worker.service /etc/systemd/system/
printf '%s\n' "$role" > /etc/cloudsync/role
systemctl daemon-reload
if [ "$role" = server ]; then systemctl enable --now cloudsync-api; fi
systemctl enable --now cloudsync-worker
if [ "$role" = server ]; then
  for attempt in $(seq 1 45); do
    if curl -fsS http://127.0.0.1:8080/api/health >/dev/null 2>&1; then
      echo 'CloudSync attivo sulla porta 8080. Credenziali in /etc/cloudsync/server.env.'
      exit 0
    fi
    sleep 1
  done
  echo 'Avvio non completato. Consulta journalctl -u cloudsync-api.' >&2
  exit 1
fi
echo 'Worker avviato. Verifica lo stato online nel pannello amministratore.'
