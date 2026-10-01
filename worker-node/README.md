# ◈ CloudSync — Worker Node & Terminal Dashboard

Run a distributed **CloudSync Worker Node** on any external machine (macOS, Linux, Raspberry Pi, VPS, or Windows WSL) to offload data transfers from the main server.

Includes an interactive, full-screen **Terminal TUI Dashboard** (inspired by `btop` / `htop`) with live gauges, active job progress bars, transfer speeds, and real-time bandwidth meters.

---

## 🖥️ Terminal Dashboard Features

- **🌐 Live Cluster Telemetry:** API endpoint, active latency / ping, uptime, and communication health.
- **⚡ Slot Utilization Gauge:** Real-time visual progress of active worker slots (e.g. `2/4 slots - 50%`).
- **🚀 Bandwidth Saturation Meter:** Live throughput vs allocated node limit (e.g. `24.5 MiB/s / 50.0 MiB/s - 49.0%`).
- **📊 Active Job Progress Table:**
  - Job ID & Action (`COPY`, `MOVE`, `LIST`, `MKDIR`, `DELETE`)
  - Source ➔ Destination with cloud provider names
  - Visual block progress bar (`[████████░░] 82.5%`)
  - Transferred / Total bytes with instantaneous transfer speed
  - Estimated Time of Arrival (ETA) and current active filename
- **📋 Completed Transfers History:** Rolling log of recent tasks with duration, transferred bytes, and outcome status.
- **🛡️ Graceful Shutdown & Failover:** Pressing `Ctrl + C` automatically releases in-flight jobs back to the central queue without error codes or progress loss.

---

## 📦 Requirements

- **Python 3.10+**
- **Rclone** (installed and available in system `PATH`):
  - macOS: `brew install rclone`
  - Debian / Ubuntu: `sudo apt install rclone`
  - Generic: `curl https://rclone.org/install.sh | sudo bash`
- Python dependencies:
  ```bash
  pip install httpx rich
  ```

---

## 🚀 Quick Start

### 1. Configure the Node

Copy the template environment file:
```bash
cp .env.example .env
```

Edit `.env` with your settings:
```env
NODE_NAME=mac-mini
CONTROL_URL=http://192.168.1.98:8080
WORKER_TOKEN=<token_from_admin_dashboard>
WORKER_SLOTS=4
BANDWIDTH_LIMIT_MBPS=50.0
```

> **Tip:** If the node is outside the local network, point `CONTROL_URL` to your public Cloudflare Tunnel or domain (e.g. `https://sync.example.com`).

### 2. Launch the Worker Node

Run using the startup script:
```bash
chmod +x start_node.sh
./start_node.sh
```

Or execute directly with Python:
```bash
python3 node_dashboard.py
```

To gracefully stop the node at any time, press `Ctrl + C`.
