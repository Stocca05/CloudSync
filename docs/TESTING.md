# Verifiche · Evidenze e limiti

[← Panoramica](../README.md)

## Suite automatica

```bash
uv sync --frozen
uv run pytest -q
uv run ruff check cloudsync tests
node --check frontend/app.js
bash -n start deploy/install-lxc.sh
```

| Area | Verifica |
| --- | --- |
| Account | Username/password semplici, hash e logout |
| Isolamento | Un utente non legge né modifica lavori e collegamenti altrui |
| Ingressi | Origine richieste, provider consentiti, percorsi relativi, host privati negati |
| Lease | Nessuna doppia acquisizione, rifiuto di token scaduti e completamenti duplicati |
| Coda | Annullamento, nuovo tentativo, recupero dopo worker perso |
| Banda | Pesi per utente, tetti, somma delle assegnazioni entro il budget |
| Pianificazioni | Nessuna sovrapposizione di esecuzioni |
| Segreti | Configurazioni cifrate, credenziali escluse dalle risposte |
| Rclone reale | Copia di file e sottocartelle, confronto SHA256, esplorazione della destinazione |

## Concorrenza PostgreSQL

Usare un database di test accessibile all'utente indicato. Il test crea uno schema casuale e rimuove soltanto quello.

```bash
TEST_DATABASE_URL='postgresql+psycopg://utente:password@localhost:5432/cloudsync_test' \
  uv run pytest tests/test_postgres.py -q -s
```

Risultato del collaudo locale del 30 settembre 2026:

- 40 utenti e 16 identità worker.
- 120 lavori in coda.
- 96 richieste di acquisizione, con 16 thread concorrenti.
- 64 assegnazioni uniche, nessuna duplicata.
- Massimo 2 lavori per utente e 4 per nodo rispettato.
- Tutti i 40 utenti hanno ottenuto almeno un turno.
- Tempo osservato per inizializzazione/acquisizioni: circa 5,2 secondi nella prova iniziale.

Questo è un test del coordinamento, **non un benchmark di 64 trasferimenti cloud simultanei**. La capacità reale dipende da RAM, CPU, banda e provider. Il container fornito ha risorse sufficienti per iniziare con due lavori contemporanei; non va equiparato al cluster simulato.

## Browser reale

```bash
uv run python tests/ui_check.py
```

Il test avvia un'app temporanea su loopback e usa Chrome headless. Prova login, creazione di un collegamento con credenziali fittizie, modifica del limite del cluster, arruolamento nodo e viewport mobile. Controlla assenza di errori JavaScript e overflow orizzontale. Le schermate sono in `artifacts/`, escluso da Git; il README contiene una schermata della dashboard vuota.

## Da non confondere con verifiche già concluse

Le integrazioni cloud richiedono ancora account reali del proprietario per un collaudo specifico. Il test browser non autentica Google Drive. La procedura iCloud è verificata con risposte Rclone/Apple simulate, ma non ancora con un account Apple reale. OAuth web Google diretto non è implementato. Egress firewall e prova su un secondo container Proxmox devono essere verificati nella topologia definitiva.


## Aggiornamento iCloud e verifica finale

La suite aggiunge due test per la configurazione `iclouddrive`: isolamento della richiesta 2FA, cifratura della risposta, conferma di ricezione e protocollo `config/create → config/update → config/get`. Il binario Rclone 1.75.1 installato espone i campi `apple_id`, `password`, `trust_token`, `cookies` e `client_id` attesi.

Ripristinato l’accesso di rete, il 30 settembre 2026 la suite completa è passata: **19 test, inclusi PostgreSQL e copia reale Rclone**. Il collaudo Chrome è passato senza errori JavaScript. Queste verifiche non sostituiscono un collaudo del flusso Apple con un account reale.

## Container di destinazione

Il collaudo `scripts/deployment_smoke.py` eseguito sul container Debian 12 usa due utenti, due processi worker reali e quattro copie da 2 MiB con confronto SHA256. Il worker temporaneo viene interrotto durante un lavoro: la coda lo recupera e il worker locale completa la copia. Il test rimuove soltanto le proprie fixture. Il servizio worker dispone di scrittura in `/var/lib/cloudsync`, mantenendo il resto del filesystem protetto. I due worker di questa prova condividono il container: non è una verifica multinodo fisica.

Il collaudo su loopback richiede temporaneamente `COOKIE_SECURE=false`; completarlo prima di attivare i cookie HTTPS per l’accesso pubblico.

## Accessi cloud e amministrazione (aggiornamento)

28 test passati con PostgreSQL: aggiunte prove di OAuth Google (state/sessione/PKCE, scadenza e replay), associazione Rclone monouso e revoca al logout, conservazione della sessione Apple, aggiornamento credenziali con isolamento, monitoraggio amministrativo e variazione delle quote per priorità dei lavori. Browser desktop/mobile: nessun campo token per Google, creazione collegamento S3, modifica priorità, limiti e arruolamento nodo. Avvio reale `rclone authorize drive` verificato fino al redirect Google con client condiviso e callback locale; consenso Google e accesso Apple reale restano da completare con il titolare.
