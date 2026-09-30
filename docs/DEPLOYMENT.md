# Distribuzione · Dal primo container al cluster

[← Panoramica](../README.md) · [Operazioni →](OPERATIONS.md)

## Topologia iniziale su Proxmox

```text
Proxmox
└── LXC Debian 12 dedicato
    ├── cloudsync-api.service     :8080
    ├── PostgreSQL               127.0.0.1:5432
    └── cloudsync-worker.service  connessioni in uscita
```

L'installazione nativa evita Docker annidato nel container. Il programma non richiede accesso all'host Proxmox, mount FUSE, socket Docker o privilegi root durante l'esecuzione. L'installer rifiuta un host con `/etc/pve`.

Per un piccolo gruppo, partire da 2–4 vCPU, 2 GB di RAM consigliati e 10 GB di disco oltre al sistema. Un LXC da 1,5 GB può ospitare un'istanza iniziale con due lavori contemporanei, ma va misurato sotto carico. La coda può contenere molti utenti senza avviare tutti i loro lavori insieme.

## Installazione nativa

All'interno di un LXC dedicato Debian/Ubuntu con systemd, come root:

```bash
cd /percorso/CloudSync
./start native
```

L'installer:

1. Installa i prerequisiti e PostgreSQL.
2. Copia il programma in `/opt/cloudsync`.
3. Installa Python 3.12 tramite uv e le dipendenze dal lockfile.
4. Scarica Rclone 1.75.1 e verifica l'archivio con lo SHA256 ufficiale.
5. Genera account iniziale, chiave di cifratura e token del worker.
6. Installa e abilita le unità systemd, con utente non privilegiato e filesystem protetto.
7. Verifica `/api/health`.

Da quel momento (non interrompe servizi già attivi):

```bash
cd /opt/cloudsync
./start
```

L'indirizzo sulla LAN è `http://IP-DEL-CONTAINER:8080`. Le credenziali amministrative sono in `/etc/cloudsync/server.env`, leggibile da root. API e worker ripartono automaticamente al riavvio del container.

L'installer non è un gestore di aggiornamenti né un migratore di database: vedi [Operazioni](OPERATIONS.md). Non installarlo direttamente su un server che ospita un database `cloudsync` preesistente: il controllo interrompe l'operazione per evitare di modificarlo.

## Accesso Internet

Usare un dominio HTTPS stabile tramite reverse proxy o tunnel gestito. Il progetto non crea un Quick Tunnel pubblico all'avvio.

Esempio Caddy su una macchina già predisposta come reverse proxy:

```caddy
cloud.example.net {
    reverse_proxy 192.168.1.98:8080
}
```

Quando HTTPS è disponibile, impostare `COOKIE_SECURE=true` nel file del server e riavviare l'API. Il proxy deve preservare l'header Host. Limitare l'accesso diretto alla porta 8080 alla rete fidata/proxy. Database e API Rclone non devono essere pubblicati.

Gli endpoint `/internal/*` richiedono il token del singolo nodo: esporli solo ai worker se il proxy consente regole per rete. Per nodi su reti diverse, usare HTTPS o una VPN; evitare token in chiaro attraverso Internet.

## Aggiungere un nodo nativo

1. Nel pannello **Cluster e utenti**, crea un nodo con nome, slot e banda.
2. Copia il token mostrato una sola volta.
3. Sul nuovo container, prepara `.env` nella directory del progetto:

```dotenv
CONTROL_URL=https://cloud.example.net
WORKER_TOKEN=token-generato-dal-pannello
WORKER_SLOTS=2
SFTP_KNOWN_HOSTS=/etc/cloudsync/known_hosts
```

4. Proteggi il file con `chmod 600 .env` e avvia:

```bash
./start native worker
```

Il nuovo nodo non installa PostgreSQL e non riceve la chiave del vault. Installa solo il worker e Rclone. Dopo l'installazione basta `./start`.

Un token identifica **una sola istanza worker**. Non clonare il medesimo token su macchine diverse: crea una nuova identità per ogni nodo. Il numero di thread `WORKER_SLOTS` e gli slot assegnabili dal pannello devono essere coerenti.

## Variante Docker

Requisiti: Docker Engine e plugin Docker Compose, daemon avviato. In una VM Proxmox dedicata:

```bash
./start
```

Il file `.env` viene creato automaticamente con permessi 600. Di default la porta viene pubblicata solo su loopback. Per accesso LAN imposta `BIND_ADDRESS=0.0.0.0`, poi rilancia `./start`.

Worker aggiuntivo:

```bash
# Prepara .env con CONTROL_URL, WORKER_TOKEN e WORKER_SLOTS
./start worker
```

Le immagini applicative sono uguali per API e worker. Rclone e uv hanno versioni esplicite; le dipendenze Python sono congelate in `uv.lock`. I tag delle immagini di base non sono ancora fissati per digest.

## SFTP e rete dei worker

Installa le chiavi host SFTP verificate in `/etc/cloudsync/known_hosts` (nativo) o `runtime/known_hosts` (Docker). Non aggiungere ciecamente una chiave ottenuta dalla rete: confrontala con quella fornita dal proprietario del server.

I campi URL/host vengono validati sia dall'API sia dal worker: niente loopback, LAN o metadata IP nelle configurazioni pubbliche. Questo non è una difesa completa contro DNS rebinding: per esposizione a utenti non fidati, configura anche firewall egress sui worker, consentendo il control plane e impedendo l'accesso alle reti di gestione/metadata. La rete del control plane non dovrebbe essere utilizzata come rete generica per i trasferimenti.

Non servono porte in ingresso sui worker. Il loro Rclone RC ascolta solo su loopback, con una password casuale per esecuzione.

## Link Cloudflare temporaneo

Per un accesso immediato senza dominio, è disponibile `deploy/cloudsync-tunnel.service`. Richiede `cloudflared` in `/usr/local/bin/cloudflared`. Installa l'unità nel container e abilitala:

```bash
cp deploy/cloudsync-tunnel.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now cloudsync-tunnel
journalctl -u cloudsync-tunnel --no-pager | grep 'https://.*trycloudflare.com'
```

Imposta `COOKIE_SECURE=true` nel file `/etc/cloudsync/server.env` e riavvia `cloudsync-api` per usare cookie di accesso solo su HTTPS. Da quel momento esegui il login attraverso il link HTTPS, non tramite l'IP HTTP locale.

L'unità mantiene il processo attivo dopo la chiusura SSH e al riavvio del container. L'indirizzo generato può cambiare quando `cloudflared` riparte: per un indirizzo permanente serve un tunnel associato al tuo account e dominio Cloudflare. La registrazione semplice dell'app rimane disponibile a chi raggiunge il link.

Fonte: [Cloudflare Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).
