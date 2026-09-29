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
| Google Drive | Token JSON generato da `rclone authorize drive`; gli eventuali client ID/secret devono corrispondere a quelli usati per ottenerlo |

Per Google Drive esegui sul tuo computer:

```bash
rclone authorize drive
```

Completa il consenso nel browser e incolla il JSON del token nel campo dedicato. Trattalo come una password. CloudSync conserva gli aggiornamenti del token scritti da Rclone al termine del lavoro, senza esporlo nell'elenco dei collegamenti. Il login Google direttamente dal sito richiede ancora un'integrazione OAuth dedicata al dominio.

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
