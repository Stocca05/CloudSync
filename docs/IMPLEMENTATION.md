# CloudSync — ricostruzione distribuita

## Obiettivo
Un servizio web persistente per utenti con username/password semplici, collegamenti personali e trasferimenti Rclone eseguiti su uno o più nodi. Avvio con `./start`. Destinazione: container Proxmox con API, PostgreSQL e worker locali; ulteriori worker possono essere aggiunti su altri nodi.

## Decisioni
- PostgreSQL è la fonte autorevole per utenti, sessioni, segreti cifrati, coda, lease e worker.
- API separata dai worker; solo API e PostgreSQL conservano stato durevole.
- Worker Linux non privilegiati, configurazioni temporanee per lavoro, nessun comando shell fornito dagli utenti.
- Account senza email o requisiti di complessità; password hashate, sessioni revocabili, dati separati per proprietario.
- Coda equa per utente, pesi amministrativi e limiti di banda globali/per nodo/per utente.
- Copia ripetibile; riavvio di un lavoro interrotto con confronto Rclone, non promessa di ripresa al byte.
- Il browser non è il supervisore dei trasferimenti.
- Solo provider esplicitamente consentiti; niente importazione libera di configurazioni o filesystem host.
- Nodi worker fidati amministrati dal proprietario, non computer ostili.

## Fasi Git
1. Piano e contratto operativo.
2. Backend multiutente, coda e scheduler.
3. Worker Rclone e protocollo con lease.
4. Interfaccia e avvio riproducibile.
5. Test reali, documentazione e distribuzione.

## Verifiche richieste
Isolamento tra due utenti; due worker concorrenti; revoca lease; annullamento; riavvio; allocazione banda; copia reale con confronto hash; avvio completo da directory pulita; nessun segreto in Git.

## Distribuzione e stato
Il 30 settembre 2026 la versione con iCloud è stata distribuita nel container LXC Debian 12 indicato dal proprietario. API, worker e tunnel Cloudflare sono gestiti da systemd e abilitati all’avvio. La vecchia istanza Docker è stata fermata dopo la verifica di salute; dati legacy e database sono conservati nei backup del container.

L’accesso SSH e la rete sono nuovamente disponibili. La suite completa comprende 19 test passati, inclusi PostgreSQL concorrente e copia reale Rclone. Il collaudo browser verifica login, collegamenti, amministrazione e layout mobile.

Provider richiesti: Google Drive, S3, SFTP, WebDAV e iCloud. Il tunnel pubblico iniziale è temporaneo; per un indirizzo stabile serve un tunnel associato al proprio dominio Cloudflare. I collegamenti cloud richiedono ancora il collaudo con account reali.
