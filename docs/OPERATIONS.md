# Operazioni · Tenere il servizio in salute

[← Panoramica](../README.md) · [Architettura](ARCHITECTURE.md)

## Stato e log

| Operazione | Nativo LXC | Docker |
| --- | --- | --- |
| Avvio | `/opt/cloudsync/start` | `./start` |
| Stato | `systemctl status cloudsync-api cloudsync-worker` | `docker compose ps` |
| Log API | `journalctl -u cloudsync-api -n 100` | `docker compose logs --tail 100 api` |
| Log worker | `journalctl -u cloudsync-worker -n 100` | `docker compose logs --tail 100 worker` |
| Arresto | `systemctl stop cloudsync-worker cloudsync-api` | `docker compose stop` |
| Salute | `curl -f http://127.0.0.1:8080/api/health` | Stesso comando |

La salute dell'API verifica il database. Lo stato dei worker si vede nel pannello: un'API sana non implica che un worker sia online. I log applicativi non stampano credenziali cloud o token di arruolamento. Gli errori di trasferimento mostrati all'utente sono riassunti per evitare di esporre segreti presenti nei messaggi dei provider.

## Backup: due elementi inseparabili

1. Database PostgreSQL.
2. File dei segreti con `ENCRYPTION_KEY` e configurazione del servizio.

Senza la chiave non è possibile decifrare le credenziali salvate. Conservare il backup dei segreti separato dal dump, con permessi restrittivi e cifratura sul supporto di backup.

### Nativo

```bash
install -d -m 700 /root/cloudsync-backups
runuser -u postgres -- pg_dump -Fc cloudsync > /root/cloudsync-backups/cloudsync.dump
chmod 600 /root/cloudsync-backups/cloudsync.dump
cp -a /etc/cloudsync /root/cloudsync-backups/service-secrets
```

### Docker

```bash
mkdir -p runtime/backups
chmod 700 runtime/backups
docker compose exec -T db pg_dump -U cloudsync -Fc cloudsync > runtime/backups/cloudsync.dump
chmod 600 runtime/backups/cloudsync.dump
cp .env runtime/backups/service.env
```

Questi percorsi sono locali: copiarli anche su un altro supporto. Le cartelle `runtime` e i file `.env` sono esclusi da Git.

## Ripristino

Provare prima su un database nuovo. Fermare API e worker, ripristinare il dump con `pg_restore` e reinserire la stessa chiave di cifratura. Assicurarsi che nessun vecchio worker stia ancora eseguendo i lavori prima di avviare il clone ripristinato. Una copia di backup di produzione non va accesa con gli stessi worker su una rete attiva.

Esempio nativo, database di prova:

```bash
runuser -u postgres -- createdb cloudsync_restore_test
runuser -u postgres -- pg_restore --exit-on-error --no-owner -d cloudsync_restore_test < /root/cloudsync-backups/cloudsync.dump
```

Il test va verificato leggendo utenti/lavori e decifrando una configurazione di prova con la chiave corretta. Il ripristino dei segreti fa parte della prova.

## Aggiornamenti

Non viene eseguito `git pull` automatico all'avvio. Ogni aggiornamento è una versione da verificare.

1. Fare backup e annotare commit/versione in esecuzione.
2. Attendere il completamento dei lavori importanti oppure fermare i worker.
3. Aggiornare i file del programma senza sovrascrivere `.env` o `/etc/cloudsync`.
4. Installare dipendenze dal lockfile e applicare eventuali migrazioni indicate dalla release.
5. Avviare, verificare salute, login, coda e una copia di prova.

La 0.2 crea lo schema iniziale. **`create_all` non aggiorna colonne di un database già esistente:** le future modifiche dello schema richiederanno migrazioni esplicite. Non usare la nuova app sul database di una versione incompatibile.

Per Docker: `./start` ricostruisce l'immagine e rialza i servizi senza cancellare il volume. Non usare `docker compose down -v` come comando di aggiornamento.

Per nativo: sincronizzare il codice in `/opt/cloudsync`, eseguire `/opt/cloudsync-bootstrap/bin/uv sync --frozen --no-dev` con `UV_PYTHON_INSTALL_DIR=/opt/cloudsync-python`, poi `./start`.

## Se qualcosa non parte

| Sintomo | Controllo |
| --- | --- |
| Login torna alla schermata iniziale | `COOKIE_SECURE=true` richiede HTTPS; controlla anche Host del proxy |
| I lavori restano in coda | Worker online, token corretto, slot disponibili, account abilitato |
| Worker non autorizzato | Token revocato o nodo disabilitato: crea una nuova identità |
| Collegamento SFTP fallisce | Chiave host installata, host pubblico, credenziali e permessi |
| Google Drive fallisce dopo tempo | Token revocato/scaduto o client OAuth non corrispondente; ricollega il cloud |
| Copia molto lenta | Peso utente, tetto cluster/nodo, limiti provider e banda reale del nodo |
| Output cartella troppo grande | Specifica una sottocartella; esplorazione limitata a 1.000 elementi / 8 MB |
| Worker arrestato per memoria | Riduci slot e misura RAM, poi aumenta risorse del container se necessario |

## Dati e amministrazione

La cronologia cresce nel database: prevedere monitoraggio del disco e una futura politica di conservazione. La 0.2 non offre ancora eliminazione della cronologia, recupero password self-service, 2FA applicativa o un audit completo delle azioni amministrative. Gli account restano volutamente semplici come richiesto.
