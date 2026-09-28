# ☁️ CloudSync

> **Orchestratore containerizzato per il trasferimento privato e ad alte prestazioni da Google Drive a iCloud Drive tramite Rclone e FastAPI.**

[![Docker](https://img.shields.io/badge/Docker-Containerized-blue.svg)](https://www.docker.com/)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![Rclone](https://img.shields.io/badge/Rclone-v1.75+-3F51B5.svg)](https://rclone.org/)
[![Privacy](https://img.shields.io/badge/Privacy-100%25%20In--RAM-success.svg)](#garanzia-di-privacy-totale)

---

## 🔒 Garanzia di Privacy Totale

A differenza dei servizi SaaS commerciali che richiedono l'accesso ai tuoi dati e salvano file temporanei su server di terze parti, **CloudSync è progettato per essere totalmente privato e self-hosted**:

1. **Piping Diretto in RAM:** I dati fluiscono in streaming tra Google Drive e iCloud Drive sfruttando i buffer di memoria RAM allocati da Rclone (`--rc`). Nessun file dell'utente viene mai salvato sul disco del container.
2. **Nessuna Telemetria o Server Terzi:** Tutte le comunicazioni API avvengono esclusivamente tra il tuo container locale, le API di Google e i server di Apple.
3. **Credenziali Isolate:** I token OAuth e le password risiedono esclusivamente nel file `config/rclone.conf` montato sul tuo host locale.

---

## 🏗️ Architettura del Sistema

L'applicazione si struttura su **3 livelli sincronizzati**:

```mermaid
flowchart TD
    subgraph Host["Host Docker / Macchina Locale"]
        UI["Web Browser (Frontend SPA)<br/>Tailwind CSS + JS"]
        Conf["./config/rclone.conf<br/>(Vol. rw: token refresh)"]
        Data["./data<br/>(Vol. rw: cache / logs)"]
    end

    subgraph Container["Container Docker: CloudSync (Porta 8000)"]
        direction TB
        subgraph L3["Livello 3: Frontend Web UI"]
            Static["SPA Minimalista & Reattiva<br/>(File Explorer a doppia colonna, Progress Dashboard)"]
        end

        subgraph L2["Livello 2: Backend Orchestrator (FastAPI)"]
            API["FastAPI (Python 3.12)<br/>REST Endpoints + WebSocket / SSE"]
            Tracker["Job Tracker & Stats Streamer"]
        end

        subgraph L1["Livello 1: Rclone Remote Control Daemon"]
            Rclone["Rclone Daemon (rclone rcd)<br/>Porta interna 127.0.0.1:5572"]
            RAMPipe["RAM-Piping Engine<br/>(sync/move, operations/movefile)"]
        end
    end

    subgraph Cloud["Cloud Providers"]
        GDrive["Google Drive API<br/>(OAuth2)"]
        iCloud["iCloud Drive<br/>(WebDAV / App Password)"]
    end

    UI <-->|HTTP REST & WebSocket/SSE| API
    Static -.-> UI
    API <-->|HTTP Async httpx (127.0.0.1:5572)| Rclone
    Conf --> Rclone
    Data --> Container
    Rclone <==>|RAM Streaming Transfer| GDrive
    Rclone <==>|RAM Streaming Transfer| iCloud
```

1. **Rclone Daemon (Remote Control):** Eseguito su `127.0.0.1:5572` all'interno del container con comandi asincroni (`sync/move`, `operations/movefile`, `core/stats`).
2. **Backend FastAPI (Python 3.12):** Livello intermedio con client asincrono `httpx`, gestione della coda dei job, rilevamento e conversione formati Google Docs, ed emissione real-time via WebSocket e Server-Sent Events (SSE).
3. **Frontend SPA (Tailwind CSS + Vanilla JS):** Interfaccia a due colonne (Sorgente Google Drive e Destinazione iCloud), con selezione multipla, anteprime formati, barra di progresso, velocità MB/s e console eventi in tempo reale.

---

## 📋 Prerequisiti

- **Docker** e **Docker Compose** installati sulla macchina host.
- Un account Google con accesso a Google Drive.
- Un ID Apple con spazio su iCloud Drive e una **Password specifica per le app** generata.

---

## ⚙️ Configurazione dei Remoti (`rclone.conf`)

Prima di avviare il container, configura il file `config/rclone.conf` partendo dal template fornito:

```bash
cp config/rclone.conf.example config/rclone.conf
```

### 1. Configurazione Google Drive (`[gdrive]`)

Puoi generare la configurazione eseguendo sul tuo computer host:
```bash
rclone config
```
e selezionando `Google Drive`. Inserisci nel tuo `config/rclone.conf`:

```ini
[gdrive]
type = drive
scope = drive
client_id = TUO_CLIENT_ID.apps.googleusercontent.com
client_secret = TUO_CLIENT_SECRET
token = {"access_token":"...","token_type":"Bearer","refresh_token":"...","expiry":"2026-10-01T00:00:00Z"}
```

### 2. Configurazione iCloud Drive (`[icloud]`)

Per connettere iCloud Drive in modo trasparente e privato:
1. Accedi a [Gestione ID Apple](https://appleid.apple.com/account/manage).
2. Nella sezione **Accesso e Sicurezza**, seleziona **Password specifiche per le app**.
3. Genera una nuova password specifica (es. etichetta `cloudsync`).
4. Oscura la password tramite Rclone:
   ```bash
   rclone obscure "tua-password-specifica"
   ```
5. Inserisci il blocco in `config/rclone.conf`:
   ```ini
   [icloud]
   type = webdav
   url = https://p58-content.icloud.com
   vendor = other
   user = tuonomeutente@icloud.com
   pass = RisultatoDiRcloneObscure
   ```

> [!NOTE]
> Il volume montato su `./config` nel `docker-compose.yml` è impostato con permesso di scrittura (`:rw`) per consentire a Rclone di aggiornare automaticamente i token di refresh OAuth2 di Google Drive alla scadenza.

---

## 🚀 Avvio Rapido con Docker Compose

Avvia il container con un singolo comando:

```bash
docker compose up -d --build
```

Verifica lo stato del container:
```bash
docker compose ps
docker compose logs -f
```

Apri il browser su:
```text
http://localhost:8000
```

---

## 🖥️ Utilizzo della Web UI

1. **Stato Connessioni:** Nella barra in alto verifica che entrambi i pill `gdrive:` e `icloud:` mostrino il badge verde **Connesso** con le quote disco.
2. **Esplora Google Drive (Colonna Sinistra):** Naviga nelle cartelle sorgente. Seleziona i singoli file o intere cartelle tramite le checkbox.
   - I file Google Docs, Sheets o Slides presentano un badge distintivo: verranno esportati automaticamente nei rispettivi formati standard Microsoft Office (`.docx`, `.xlsx`, `.pptx`).
3. **Seleziona Destinazione iCloud (Colonna Destra):** Naviga nelle directory di iCloud Drive. Se necessario, utilizza il pulsante **"Nuova cartella"** per creare la directory di destinazione.
4. **Avvia il Trasferimento:** Clicca sul pulsante **"Sposta su iCloud Drive"** e conferma l'operazione nel modal.
5. **Monitoraggio Real-Time:** Osserva in tempo reale:
   - Velocità istantanea in **MB/s**
   - Dati totali trasferiti
   - File attualmente in transito con percentuali individuali
   - Log dettagliato degli eventi

---

## 🛠️ Sviluppo e Test Locale (senza Docker)

Per sviluppare ed eseguire test in locale:

### Installazione Dipendenze con `uv`
```bash
# Sincronizza l'ambiente virtuale
uv sync

# Esegui i controlli di linting e formattazione con ruff
uv run ruff check .
uv run ruff format --check .

# Esegui la suite di test
uv run pytest
```

### Avvio Demone Rclone e FastAPI in Locale
```bash
# Terminale 1: Rclone Daemon
rclone rcd --rc-addr=127.0.0.1:5572 --rc-no-auth --config=./config/rclone.conf

# Terminale 2: FastAPI Backend
uv run uvicorn backend.main:app --reload --port 8000
```

Documentazione interattiva OpenAPI / Swagger:
`http://localhost:8000/docs`

---

## 📦 Struttura del Repository

```
CloudSync/
├── Dockerfile                  # Costruzione multi-stage ottimizzata (Rclone + Python 3.12)
├── docker-compose.yml          # Definizione servizi, volumi persistenti e healthcheck
├── entrypoint.sh               # Script di avvio, healthcheck loop e graceful shutdown
├── pyproject.toml              # Definizione dipendenze gestite con uv e configurazione pytest/ruff
├── README.md                   # Documentazione di architettura e manuale utente
├── config/
│   └── rclone.conf.example     # Modello di configurazione remoti gdrive e icloud
├── data/                       # Directory per volumi persistenti e sessioni
├── backend/
│   ├── __init__.py
│   ├── config.py               # Impostazioni tipizzate con pydantic-settings
│   ├── models.py               # Schemi dati Pydantic v2
│   ├── rclone_client.py        # Client asincrono httpx per l'API RC di Rclone
│   ├── main.py                 # Applicazione FastAPI, WebSocket /ws/stats e SSE /api/stream/stats
│   └── tests/
│       ├── __init__.py
│       └── test_api.py         # Test unitari e di integrazione degli endpoint
└── frontend/
    ├── index.html              # Interfaccia SPA reattiva con Tailwind CSS
    ├── app.js                  # Logica applicativa, WebSocket/SSE e gestione selezioni
    └── styles.css              # Stili personalizzati e scrollbar
```

---

## 📄 Licenza
Rilasciato sotto licenza MIT. Libero per uso personale e commerciale.
