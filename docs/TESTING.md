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

Le integrazioni cloud richiedono ancora account reali del proprietario per un collaudo specifico. Il test browser non autentica Google Drive. iCloud e OAuth web diretto non sono implementati. HTTPS pubblico, egress firewall e prova su un secondo container Proxmox devono essere verificati nella topologia definitiva.
