# Collegare Google Drive e iCloud

[← Panoramica](../README.md)

## Google: il metodo Rclone

Il pulsante **Accedi con Google** usa per impostazione predefinita `rclone authorize drive` e il client condiviso di Rclone. Nessun client ID, file JSON o token da inserire nel sito.

Il ritorno OAuth di Rclone è `http://127.0.0.1:53682/`: deve arrivare sul computer dove si trova il browser. Per questo CloudSync usa un assistente locale, non il worker nel container. L’assistente apre Google e consegna automaticamente l’autorizzazione a CloudSync tramite HTTPS. Confermare il nome utente CloudSync mostrato dall’assistente prima di scegliere l’account Google.

Sul Mac dell’operatore l’assistente viene installato come LaunchAgent dell’utente. Per gli altri computer, installare [Python 3](https://www.python.org/downloads/) e [Rclone](https://rclone.org/install/), scaricare l’assistente da Collegamenti → Google Drive e avviarlo:

```bash
python3 CloudSync-Google.py
```

La copia scaricata contiene già l’indirizzo del servizio, senza credenziali. La versione nel repository richiede:

```bash
python3 scripts/google_helper.py --site https://cloud.example.org
```

Il programma ascolta soltanto su `127.0.0.1:53683`, controlla Host e Origin, richiede conferma locale con nonce monouso e accetta un login Google alla volta per computer. I diversi computer possono autorizzarsi contemporaneamente. Il ticket di associazione dura dieci minuti, appartiene alla sessione CloudSync che lo ha richiesto, viene invalidato dal logout e può salvare un solo collegamento. Una risposta HTTP persa può essere ritrasmessa senza duplicarlo. Il sito resta aperto e verifica sul server lo stato dell’autorizzazione. L’assistente rifiuta una richiesta destinata a un indirizzo diverso da quello configurato e mostra come aggiornarlo. Token e output Rclone non vengono scritti su disco o nei log; sul server il token è cifrato.

Non è un login browser-only su dispositivi senza assistente, inclusi telefoni. Rclone segnala inoltre la dismissione del suo client condiviso durante il 2026: questo metodo dipende dalla sua disponibilità. La verifica di avvio ha confermato che Rclone genera l’URL Google con il client condiviso, ma non sostituisce il consenso reale dell’account.

Fonti: [configurazione remota Rclone](https://rclone.org/remote_setup/), [client Google Drive Rclone](https://rclone.org/drive/#making-your-own-client-id).

### Alternativa web con configurazione centralizzata

Il servizio supporta anche il normale flusso web Google senza assistente. Richiede una configurazione unica dell’amministratore, non di ogni utente:

```dotenv
PUBLIC_URL=https://cloud.example.org
GOOGLE_CLIENT_ID=client-web-dell-app
GOOGLE_CLIENT_SECRET=segreto-dell-app
```

Registrare `https://cloud.example.org/api/google/callback` come redirect del client web e abilitare Google Drive API. Configurare il consenso e gli utenti di test o la pubblicazione secondo le richieste Google. Riavviare l’API. State monouso, associazione alla sessione e PKCE proteggono il ritorno OAuth. I token sono cifrati e il client dell’app è conservato insieme al collegamento per il rinnovo nei worker.

[Flusso web Google ufficiale](https://developers.google.com/identity/protocols/oauth2/web-server).

## iCloud: account Apple, poi verifica

Usare email e password normale dell’account Apple. Le password specifiche per app non sono accettate dal backend Rclone. Approvare l’accesso sul dispositivo e inserire il codice più recente; in alternativa scegliere **Invia un codice via SMS**. Le richieste di scelta del numero vengono mostrate quando Apple offre più numeri fidati.

La continuazione 2FA conserva la configurazione Rclone della stessa sessione: non reinvia password, cookie vecchi o token precedenti. Un nuovo collegamento riparte dalle credenziali Apple, senza cookie obsoleti. Le risposte 2FA restano cifrate e sono eliminate dopo la conferma del worker.

**Aggiorna credenziali** permette di correggere email/password senza creare un altro collegamento; annullare prima eventuali lavori attivi o in coda sul collegamento. Gli errori distinguono credenziali rifiutate, codice errato, sessione scaduta, limiti Apple e accesso web. I log contengono solo ID del lavoro e categoria, senza risposte grezze Apple.

Abilitare **Accesso ai dati iCloud sul web** nelle impostazioni Apple e approvare le eventuali richieste sul dispositivo. [Documentazione iCloud di Rclone](https://rclone.org/iclouddrive/).

La prova completa con un account Apple reale richiede l’intervento del titolare per la verifica 2FA. I test automatici coprono il protocollo e l’isolamento; non certificano l’accesso di uno specifico account Apple.

## Amministrazione dei processi

La pagina **Amministrazione** mostra i lavori di tutti gli utenti, inclusi accessi cloud e letture cartelle, con filtro di stato e pagine da 100 elementi. L’amministratore vede utente, nodo, byte, velocità e quota assegnata; può interrompere un lavoro e cambiare la sua priorità.

La priorità bassa/normale/alta corrisponde a pesi 1/2/3 nella suddivisione della banda tra lavori attivi dello stesso utente e sul nodo. Le quote globali, per utente e per nodo restano rispettate. In coda ordina i lavori dello stesso utente, mantenendo la rotazione equa tra utenti. La nuova quota viene applicata al successivo heartbeat del worker; non è una modifica della priorità CPU Linux.

### Se il tunnel cambia indirizzo

Aggiornare `PUBLIC_URL` sul server e l’argomento `--site` dell’assistente locale. Sul Mac installato aggiornare `~/Library/LaunchAgents/it.cloudsync.google-helper.plist` e ricaricare il LaunchAgent. Gli assistenti scaricati prima del cambio conservano il vecchio indirizzo: scaricarli nuovamente. Il flusso rileva ora questa differenza prima di richiedere credenziali Google. Un dominio stabile evita questo problema.
