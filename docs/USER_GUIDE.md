# Guida utente · Dal collegamento alla prima copia

[← Panoramica](../README.md)

## 1. Entra nel tuo spazio

Inserisci nome e password. Se non hai ancora un account, compila gli stessi campi e premi **Prima volta? Crea un account**. Non servono email, codici o password con una particolare complessità. Le credenziali cloud sono separate da quelle di accesso a CloudSync.

## 2. Collega i servizi

Apri **Collegamenti → Collega un cloud**, scegli il provider e un nome riconoscibile. Salvare la configurazione non dichiara che l'accesso sia riuscito: apri una cartella per provarlo.

| Servizio | Cosa inserire |
| --- | --- |
| S3 | Access key, secret key, regione e, per servizi compatibili, endpoint HTTPS |
| WebDAV | URL HTTPS del servizio, utente, password; vendor `other`, `nextcloud`, `owncloud` o `sharepoint` |
| SFTP | Host pubblico, porta, utente e password. L'amministratore deve installare la chiave host verificata su ogni worker |
| iCloud Drive | Apple ID e password principale Apple, poi codice 2FA nel dialogo guidato |
| Google Drive | Token JSON generato da `rclone authorize drive`; gli eventuali client ID/secret devono corrispondere a quelli usati per ottenerlo |

Per Google Drive esegui sul tuo computer:

```bash
rclone authorize drive
```

Completa il consenso nel browser e incolla il JSON del token nel campo dedicato. Trattalo come una password. CloudSync conserva gli aggiornamenti del token scritti da Rclone al termine del lavoro, senza esporlo nell'elenco dei collegamenti. Il login Google direttamente dal sito richiede ancora un'integrazione OAuth dedicata al dominio.

### iCloud Drive e verifica Apple

Dopo aver salvato Apple ID e password, CloudSync avvia un processo Rclone privato e mostra il passaggio richiesto da Apple. Inserisci il codice 2FA ricevuto sul dispositivo fidato. Se Rclone propone altre scelte, la finestra presenta le opzioni disponibili. Non usare password specifiche per app: il backend ufficiale richiede la password dell'account Apple e 2FA.

La procedura scade dopo 10 minuti e non viene ritentata automaticamente per evitare richieste ripetute ad Apple. Puoi riaprirla dal pulsante **Collega / rinnova Apple**. Se un trasferimento sul collegamento è attivo, attendine la fine prima di rinnovare l'accesso. Il codice 2FA viene cifrato nella coda e rimosso dopo la conferma del worker.

L'accesso ai dati iCloud sul Web deve essere consentito nelle impostazioni Apple; se è attiva la Protezione avanzata dei dati, Apple può richiedere un'ulteriore approvazione sul dispositivo. Le richieste dipendono dall'account e dalla versione Rclone. Vedi [documentazione ufficiale iCloud](https://rclone.org/iclouddrive/).

## 3. Scegli sorgente e destinazione

La pagina **Esplora e trasferisci** ha due pannelli. I percorsi sono relativi alla radice del collegamento: scrivi `Foto/2026`, non un percorso del server.

- **Cartella:** copia il contenuto della cartella sorgente nel percorso destinazione. Per mantenere il nome `2026`, includilo nella destinazione.
- **Singolo file:** seleziona la casella e inserisci il nome completo del file anche nel percorso destinazione.
- **S3:** il primo componente del percorso è normalmente il bucket.

I collegamenti selezionati devono essere distinti. L'esplorazione viene eseguita dai worker: se tutti gli slot sono occupati, anche la lettura resta in coda.

## 4. Avvia e lascia lavorare il server

**Copia** conserva gli originali. **Sposta** rimuove dalla sorgente i file trasferiti con successo e richiede una conferma. La verifica di Rclone dipende dalle capacità del provider: non tutti espongono lo stesso tipo di checksum.

La priorità ordina i lavori dentro la tua coda. La quota di banda fra utenti è stabilita dall'amministratore. Puoi chiudere il browser o uscire dall'account: i lavori continuano sul server.

Nella panoramica trovi stato, nodo, dati trasferiti e velocità. **Annulla** chiede al worker di fermarsi; non annulla i file già copiati e non ripristina quelli già spostati. **Riprova** riparte confrontando sorgente e destinazione.

## 5. Ripeti una copia

Nella stessa pagina imposta un intervallo di almeno 5 minuti e premi **Crea pianificazione**. La prima copia viene messa in coda al prossimo controllo del worker. Non vengono sovrapposte due esecuzioni della stessa pianificazione.

Eliminare una pianificazione impedisce le esecuzioni future, ma non cancella un lavoro già partito: annullalo dalla panoramica.

## Disconnettere un cloud

**Rimuovi** disattiva il collegamento, elimina le credenziali memorizzate e annulla i lavori pendenti collegati; la cronologia resta consultabile. Per revocare anche il consenso del servizio cloud usa il pannello del provider.
