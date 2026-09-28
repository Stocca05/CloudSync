# ☁️ CloudSync

> **Orchestratore containerizzato per il trasferimento privato e ad alte prestazioni da Google Drive a iCloud Drive tramite Rclone, FastAPI e Cloudflare Tunnel.**

[![CI/CD Pipeline](https://github.com/Stocca05/CloudSync/actions/workflows/ci.yml/badge.svg)](https://github.com/Stocca05/CloudSync/actions)
[![Docker](https://img.shields.io/badge/Docker-Containerized-blue.svg)](https://www.docker.com/)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![Rclone](https://img.shields.io/badge/Rclone-v1.75+-3F51B5.svg)](https://rclone.org/)
[![Cloudflare Tunnel](https://img.shields.io/badge/Cloudflare-Quick%20Tunnel-orange.svg)](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/)
[![Privacy](https://img.shields.io/badge/Privacy-100%25%20In--RAM-success.svg)](#-garanzia-di-privacy-totale)

---

## ⚡ Avvio con 1 Singolo Click (Zero Configurazione)

Per avviare l'intero stack (Docker, Rclone, Backend FastAPI, Web UI e Tunnel Cloudflare) con apertura automatica del browser:

| Sistema Operativo | File da cliccare due volte | Comando da terminale |
| :--- | :--- | :--- |
| **🍎 macOS** | Doppio click su **`start.command`** | `./start.sh` |
| **🪟 Windows** | Doppio click su **`start.bat`** | `start.bat` |
| **🐧 Linux** | Doppio click su **`start.sh`** o **`CloudSync.desktop`** | `./start.sh` |

Lo script avvia il container, estrae il **link HTTPS temporaneo Cloudflare** (`https://...trycloudflare.com`) e apre automaticamente l'interfaccia nel tuo browser!

---

## 🌐 Accesso Globale Tramite Cloudflare Tunnel

All'avvio, il container inizializza automaticamente un **Quick Tunnel crittografato Cloudflare**:
- Genera un URL pubblico protetto da HTTPS (es. `https://random-name.trycloudflare.com`).
- Ti consente di accedere alla Web UI e monitorare i trasferimenti dal tuo smartphone, tablet o laptop ovunque ti trovi, **senza dover aprire porte sul router (port-forwarding)** né avere un IP pubblico statico.
- Il link viene visualizzato direttamente nella console di avvio e nell'intestazione della Web UI con il pulsante per copiarlo negli appunti.

---

## 🔒 Garanzia di Privacy Totale

A differenza dei servizi commerciali terzi che memorizzano file temporanei non cifrati, **CloudSync è progettato per garantire privacy assoluta**:

1. **Piping Diretto in RAM:** I dati fluiscono in streaming tra Google Drive e iCloud Drive sfruttando esclusivamente i buffer di memoria RAM allocati da Rclone (`sync/move`). Nessun file dell'utente viene mai salvato sul disco del container o su server intermedi.
2. **Nessun Server Terzo né Telemetria:** Tutte le comunicazioni API avvengono rigorosamente tra il tuo container locale, le API ufficiali di Google e i server Apple.
3. **Credenziali Isolate:** I token OAuth e le password risiedono esclusivamente nel file locale `config/rclone.conf` montato sul tuo host.

---

## 🏗️ Architettura del Sistema

```mermaid
flowchart TD
    subgraph Internet["Accesso Utente"]
        Browser["Web Browser (SPA Tailwind CSS)"]
        CF["Cloudflare Quick Tunnel (HTTPS)<br/>*.trycloudflare.com"]
    end

    subgraph Container["Container Docker: CloudSync (Porta 8000)"]
        direction TB
        subgraph L3["Livello 3: Frontend Web UI"]
            UI["SPA Reattiva (Dual-Column Explorer,<br/>Progress Monitor, Modal Config, Storico)"]
        end

        subgraph L2["Livello 2: Backend Orchestrator (FastAPI)"]
            FastAPI["FastAPI (Python 3.12)<br/>REST Endpoints + WebSocket / SSE"]
            TunnelWatcher["Cloudflare Tunnel Watcher"]
            History["Audit Log & History Store (JSON)"]
        end

        subgraph L1["Livello 1: Rclone Remote Control Daemon"]
            Rclone["Rclone Daemon (rclone rcd)<br/>Porta interna 127.0.0.1:5572"]
            RAMPipe["In-RAM Stream Engine<br/>(sync/move, operations/movefile)"]
        end
    end

    subgraph Cloud["Cloud Providers"]
        GDrive["Google Drive (gdrive:)<br/>OAuth2"]
        iCloud["iCloud Drive (icloud:)<br/>WebDAV / App-Password"]
    end

    Browser <-->|Locale: http://localhost:8000| FastAPI
    Browser <-->|Remoto: HTTPS Tunnel| CF
    CF <--> FastAPI
    UI -.-> Browser
    FastAPI <-->|HTTP Async httpx (127.0.0.1:5572)| Rclone
    Rclone <==>|RAM Streaming Diretto| GDrive
    Rclone <==>|RAM Streaming Diretto| iCloud
```

---

## 🔄 Scelta Modalità: Sposta vs Copia

Nella barra strumenti dell'applicazione puoi scegliere in ogni momento la modalità operativa desiderata prima di avviare il trasferimento:

1. **Sposta (Elimina sorgente):**
   - Sposta i file selezionati su iCloud Drive ed **elimina i file originali da Google Drive** solo a trasferimento completato e verificato con successo.
   - Pulisce automaticamente anche le cartelle sorgente rimaste vuote.
2. **Copia (Mantieni sorgente):**
   - Duplica i file e le cartelle selezionate su iCloud Drive **mantenendo intatti tutti i file originali su Google Drive**.
   - Nessun file sorgente viene cancellato.

---

## 🔑 Accesso e Connessione con Account Google

Non ci sono file inventati o mock all'avvio: l'applicazione all'apertura mostra chiaramente lo stato di connessione e invita ad accedere con il proprio account.

### 🌟 Metodo 1: Accesso Ufficiale Google in 1-Click (Consigliato)
- **Zero configurazioni tecniche:** Nessun bisogno di creare progetti su Google Cloud, Client ID o Service Account.
- **Come funziona:**
  1. Clicca sul pulsante **"Accedi con Google"** direttamente nella colonna o nella finestra di configurazione.
  2. Si apre automaticamente la pagina ufficiale sicura di Google (`accounts.google.com`).
  3. Clicca su **"Consenti"**: il token viene catturato in sicurezza dal server locale e Google Drive è immediatamente collegato e pronto all'uso!
  4. *(Opzionale per smartphone/dispositivi remoti)*: Se la pagina di callback locale non è raggiungibile dal tuo telefono, puoi incollare l'URL o il codice reindirizzato nel box dedicato.

### Metodo 2: Opzioni Avanzate (Service Account o Token OAuth JSON)
- **Service Account Google Cloud:** Carica o incolla il file `.json` della tua Service Account Google Cloud se desideri un accesso persistente e illimitato legato a un progetto specifico.
- **Token OAuth JSON Diretto:** Incolla una stringa di token pre-generata (`{"access_token": "...", ...}`).

---

## ☁️ Configurazione iCloud Drive (Destinazione)

Per collegare iCloud Drive hai due opzioni immediate:

1. **Account Apple (Cloud):**
   - Vai su [appleid.apple.com](https://appleid.apple.com/account/manage) e genera una **Password specifica per le app** (es. con etichetta "cloudsync").
   - Inserisci la tua email Apple ID e la password generata.
   - La password viene oscurata e protetta da Rclone, testando immediatamente la connessione.
2. **Cartella Locale Sincronizzata (Zero Rete):**
   - Se utilizzi un Mac o un PC con l'app desktop di iCloud già installata, puoi collegare direttamente il percorso della cartella locale di iCloud (es. `~/Library/Mobile Documents/com~apple~CloudDocs`), senza passare per la rete!

---

## 📁 Importazione Diretta `rclone.conf`

Se possiedi già una configurazione Rclone sul tuo computer, puoi caricare o incollare il file `rclone.conf` nel tab **"Importa rclone.conf"** del pannello *Gestione Account*: CloudSync caricherà tutti i tuoi remoti configurati all'istante.

---

## 🖥️ Funzionalità Principali della Web UI

- **Scelta Sposta / Copia:** Toggle dinamico per decidere se eliminare o preservare i dati originali su Google Drive.
- **Esplora File a 2 Colonne:** Navigazione ad albero con breadcrumb per Google Drive (sorgente) e iCloud Drive (destinazione).
- **Zero File Fittizi:** Se un provider non è configurato, mostra la schermata di login pulita per collegarsi all'istante.
- **Esportazione Automatica Google Docs:** Riconoscimento intelligente dei formati proprietari di Google (`.gdoc`, `.gsheet`, `.gslides`) ed esportazione automatica nei rispettivi standard Microsoft Office (`.docx`, `.xlsx`, `.pptx`).
- **Modalità Simulazione (Dry Run):** Possibilità di simulare il trasferimento per verificare i percorsi e le autorizzazioni senza alterare i dati.
- **Controllo di Banda Dinamico:** Selettore di velocità (Illimitata, 50 MB/s, 15 MB/s, 5 MB/s, 1 MB/s).
- **Dashboard Real-Time:** Velocità in MB/s, percentuale di avanzamento, tempo trascorso, ETA stimato e tabella dei file in transito.
- **Storico Trasferimenti Persistente:** Registro cronologico consultabile in qualsiasi momento dei batch eseguiti con indicazione se si è trattato di spostamento o copia.

---

## 🛠️ Comandi Utili

```bash
# Avvio rapido con link Cloudflare
./start.sh

# Arresto dell'applicazione
docker-compose down

# Visualizzazione dei log in tempo reale
docker logs -f cloudsync

# Esecuzione dei test unitari in locale
uv run pytest

# Controllo linting e stile con Ruff
uv run ruff check .
```

---

## 📦 Struttura del Progetto

```
CloudSync/
├── start.sh                    # Launcher universale Linux con estrazione tunnel
├── start.command               # Launcher 1-click macOS Finder (doppio click)
├── start.bat                   # Launcher 1-click Windows Explorer (doppio click)
├── CloudSync.desktop           # Desktop shortcut per Linux GNOME/KDE
├── Dockerfile                  # Costruzione multi-stage (Rclone + Cloudflared + Python 3.12)
├── docker-compose.yml          # Definizione servizi con volumi rw e healthcheck
├── entrypoint.sh               # Gestione avvio Rclone RC, FastAPI e Cloudflare Tunnel
├── pyproject.toml              # Specifiche dipendenze e configurazione pytest/ruff
├── README.md                   # Documentazione completa di architettura e utilizzo
├── .github/workflows/ci.yml    # Pipeline di integrazione continua (GitHub Actions)
├── config/
│   └── rclone.conf.example     # Modello di configurazione remoti gdrive e icloud
├── data/                       # Volume montato per log, sessioni e URL tunnel
├── backend/
│   ├── config.py               # Impostazioni tipizzate con pydantic-settings
│   ├── demo.py                 # Generatore di ambiente dimostrativo locale
│   ├── history.py              # Gestore persistente dello storico trasferimenti
│   ├── main.py                 # REST API FastAPI, WebSocket /ws/stats e SSE
│   ├── models.py               # Schemi dati Pydantic v2
│   ├── rclone_client.py        # Client HTTP asincrono per Rclone Remote Control
│   └── tests/
│       └── test_api.py         # Suite completa di test unitari (8/8 passati)
└── frontend/
    ├── index.html              # Interfaccia SPA con Tailwind CSS e Dark Mode
    ├── app.js                  # Logica applicativa, WebSocket/SSE e controller UI
    └── styles.css              # Personalizzazioni grafiche e scrollbar
```

---

## 📄 Licenza
Rilasciato sotto licenza MIT. Libero per uso personale e commerciale.
