# PhoneCam

PhoneCam usa la fotocamera e, facoltativamente, il microfono di un telefono
Android come periferiche per Linux Mint. Sul telefono basta Chrome. Il
ricevitore presenta **PhoneCam Camera** e **PhoneCam Microphone** a browser,
app di videoconferenza e registratori che supportano V4L2 e PipeWire/PulseAudio.

## Come funziona

```text
GitHub Pages (HTTPS) → carica HTML, CSS e JavaScript in Chrome
Chrome ↔ HTTP sulla LAN ↔ ricevitore Python (associazione e signaling)
Chrome → WebRTC sulla LAN → ricevitore Python
                                ├─ FFmpeg → v4l2loopback
                                └─ PCM → PipeWire → sorgente virtuale
```

Il browser usa `getUserMedia()` dalla pagina HTTPS. La pagina invia richieste
`fetch` all'IPv4 privato del PC dichiarando `targetAddressSpace: "local"`.
Chrome chiede il permesso **Rete locale** e può consentire HTTP verso un
destinatario LAN. Il ricevitore abilita CORS per l'origine esatta configurata.
Il supporto Local Network Access è stato introdotto in Chrome 142. Usa Chrome
Android aggiornato, con l'accesso alla rete locale consentito per il sito.
[Note di Chrome 142](https://developer.chrome.com/release-notes/142#local_network_access_restrictions) ·
[Guida Chrome a Local Network Access](https://developer.chrome.com/docs/modern-web-guidance/guides/security/local-network-access)

Aprire `http://IP_DEL_PC:8765` in Chrome Android non fornisce il contesto
sicuro richiesto per la cattura. Usa la pagina HTTPS. Se il browser o una
policy aziendale impedisce l'accesso HTTP alla LAN, aggiorna Chrome o verifica
il permesso del sito. Un browser privo dell'eccezione LNA richiederebbe un
endpoint locale HTTPS attendibile per lo stesso signaling automatico.
[Specifica Media Capture and Streams](https://w3c.github.io/mediacapture-main/#dom-mediadevices-getusermedia)

Sul PC, Zenity mostra la richiesta e un codice casuale di 20 caratteri.
Il codice rimane sul PC e nel browser. La conferma, l'offerta, la risposta
e la chiusura usano HMAC-SHA-256 su quella sessione. L'autorizzazione richiede
anche la corrispondenza tra l'IP sorgente, l'origine configurata e la sessione.
Il codice scade con la richiesta o il collegamento. È permesso un solo
collegamento alla volta.

Il signaling HTTP è leggibile da chi intercetta la LAN. Le descrizioni sono
firmate, inclusi i fingerprint DTLS da cui dipende l'autenticazione del
trasporto dei media. Audio e video viaggiano cifrati con WebRTC. Non ci sono
server STUN/TURN esterni, registrazioni su disco o servizi remoti di signaling.
GitHub Pages ospita soltanto i file statici. Dopo il caricamento della pagina
i due dispositivi comunicano sulla LAN.

## Requisiti

- Linux Mint 22.2 MATE o un'altra release Mint 22.x con systemd utente,
  PipeWire, WirePlumber e la compatibilità PulseAudio attiva.
- Python 3 e `venv`, FFmpeg, Zenity, `v4l2loopback` per il kernel in uso,
  `pw-loopback`, `pactl` e `pacat`.
- Chrome Android aggiornato. Telefono e PC sulla stessa LAN IPv4 privata,
  senza isolamento dei client. Il PC può anche essere collegato via cavo allo
  stesso router. Non servono applicazioni Android, inoltri di porta o account
  per usare la pagina pubblica.

## Pubblicare la pagina

1. Metti questo progetto in un repository GitHub di cui controlli le
   modifiche. Usa il branch `main`.
2. In **Settings → Pages → Build and deployment → Source**, scegli
   **GitHub Actions**.
3. Avvia il workflow **Pubblica PhoneCam su GitHub Pages** dalla scheda
   **Actions**, oppure fai un push su `main` con modifiche a `web/`.
4. Apri l'URL pubblicato, per esempio `https://utente.github.io/phonecam/`.
   L'origine da passare al ricevitore è `https://utente.github.io`, senza
   percorso o slash finale. Usa l'origine effettiva se hai un dominio
   personalizzato.

Il workflow pubblica `web/` direttamente. Puoi anche caricare il suo contenuto
su un altro host statico HTTPS di tua fiducia, configurando l'origine esatta.
[Configurazione di GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)

## Installare e avviare il ricevitore

Dalla sessione grafica, sostituisci l'origine d'esempio con quella della tua pagina.

```sh
cd "$HOME/sviluppo/Progetti/phonecam"
./install.sh https://utente.github.io --system
systemctl --user start phonecam.service
systemctl --user status phonecam.service --no-pager
journalctl --user -u phonecam.service -f
```

Con `--system` lo script usa `sudo` per i pacchetti mancanti e, se serve,
aggiunge solo `/etc/modprobe.d/phonecam.conf` e
`/etc/modules-load.d/phonecam.conf`. Installa un ambiente Python e una copia
del ricevitore in `~/.local/share/phonecam`. Senza `--system` verifica che le
dipendenze e la webcam siano già pronte. L'avvio automatico nella sessione
MATE usa la voce XDG di autostart per fornire al servizio systemd utente le
variabili della sessione grafica. La sorgente PipeWire viene creata dal
ricevitore in esecuzione e rimossa al suo arresto.

Se hai già altri dispositivi v4l2loopback, conserva le loro opzioni. Aggiungi
alla configurazione esistente una voce in `card_label` con
`PhoneCam Camera`, una voce corrispondente in `video_nr` (libera oppure
`-1` per assegnazione automatica) e `1` nella stessa posizione in
`exclusive_caps`. Adegua `devices` al totale, verifica gli indici degli
elenchi, riavvia e lancia `./install.sh https://utente.github.io`. Con una
versione di `v4l2loopback-ctl` che annunci `add` in `--help`, puoi anche
creare un dispositivo dinamico usando le opzioni mostrate dal tuo comando.
Il pacchetto base di Mint 22.x può includere un'utilità priva di `add`.
[Documentazione v4l2loopback](https://github.com/v4l2loopback/v4l2loopback#options)

La porta TCP predefinita è **8765**. Per cambiarla, usa
`systemctl --user edit phonecam.service` e aggiungi queste righe, quindi
riavvia con `systemctl --user restart phonecam.service`.

```ini
[Service]
Environment=PHONECAM_PORT=9876
```

Puoi aggiungere `Environment=PHONECAM_HOST=192.168.1.20` con il tuo IPv4
per limitare il listener a una sola interfaccia. Per provare il ricevitore
in primo piano durante lo sviluppo, ferma prima l'eventuale servizio.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m server.main --origin https://utente.github.io --port 8765
```

## Usare PhoneCam

1. Sul PC, esegui `ip -4 -br addr` e prendi l'IPv4 dell'interfaccia Wi-Fi o
   Ethernet collegata al router.
2. Apri la pagina HTTPS sul telefono. Inserisci IP e porta, seleziona
   fotocamera e risoluzione. Se vuoi anche l'audio, spunta **Microfono**.
3. Premi **Connetti** e consenti l'accesso alla **Rete locale** in Chrome
   quando richiesto.
4. Nella finestra sul PC, verifica che la richiesta coincida con la tua.
   Trascrivi il codice sul telefono e premi **Autorizza** sul PC. Sul telefono
   premi **Conferma e avvia**, quindi consenti la fotocamera ed eventualmente
   il microfono.
5. Quando compare **In trasmissione**, seleziona **PhoneCam Camera** nella
   tua applicazione Linux e, se serve, **PhoneCam Microphone**. Con
   `exclusive_caps=1` Chrome e altri programmi vedono la webcam dopo
   l'arrivo del primo fotogramma. Aggiorna l'elenco dopo l'avvio.
6. Premi **Disconnetti** sul telefono per fermare la ripresa. Durante una
   sessione con audio, la casella Microfono attiva o silenzia la traccia.
   Per aggiungere l'audio a una sessione partita senza microfono, riconnetti
   con la casella selezionata.

## Verificare le periferiche

```sh
v4l2-ctl --list-devices
pactl info
pactl list short sources
wpctl status
```

Mentre il telefono sta trasmettendo, sostituisci `/dev/videoX` con il
dispositivo PhoneCam. Puoi aprire due terminali per eseguire le verifiche.

```sh
ffplay -f v4l2 /dev/videoX
timeout 5s parecord --device=phonecam_microphone --raw --format=s16le --rate=48000 --channels=1 /dev/null
```

Per ascoltare il microfono, selezionalo in un'app di registrazione o nella
schermata di test del microfono. Il secondo comando verifica l'apertura
della sorgente e scarta i campioni. Il ricevitore converte il video in YUYV e
l'audio in PCM S16LE mono a 48 kHz. `pacat` chiede 45 ms di latenza al
server audio e scrive nel sink dedicato. La latenza complessiva dipende
anche dal buffer WebRTC e dalla rete.
[Modulo loopback di PipeWire](https://docs.pipewire.org/1.2/page_module_loopback.html)

## Problemi comuni e limiti

| Sintomo | Controllo |
| --- | --- |
| Ricevitore irraggiungibile | Tocca **Verifica il ricevitore sul Wi-Fi**. Se compare «Ricevitore PhoneCam raggiungibile», la porta HTTP è accessibile dal telefono. Controlla il permesso **Rete locale** del sito in Chrome. Evita la rete ospiti e l'isolamento Wi-Fi. Controlla nei log che l'origine sia quella installata. |
| Autorizzazione assente | Avvia il servizio dalla sessione grafica, verifica `zenity` e le variabili `DISPLAY` o `WAYLAND_DISPLAY`. La richiesta scade dopo 90 secondi. Se annulli sul telefono prima di associare il codice, chiudi anche la finestra sul PC o attendi la scadenza. |
| WebRTC non si collega | Consenti la porta TCP configurata e il traffico UDP locale per ICE sul firewall già in uso. Il ricevitore usa porte UDP effimere e candidati host. Chrome può usare mDNS per annunciare un candidato. |
| Webcam assente | Avvia la trasmissione e riapri l'elenco periferiche. Controlla `modinfo v4l2loopback`, gli header del kernel, DKMS e le impostazioni Secure Boot. Verifica i permessi del dispositivo e le autorizzazioni dell'app se è in una sandbox. |
| Webcam occupata o immagine assente dopo riconnessione | Chiudi o deseleziona la webcam nell'app Linux, poi ricomincia. Un'app che tiene aperto il dispositivo può impedire il cambio di formato. |
| Microfono assente | Controlla che `pactl info` mostri PipeWire e che `pipewire-pulse` e `wireplumber` siano in esecuzione. Scegli la sorgente **PhoneCam Microphone** e controlla che la casella sul telefono sia attiva. |
| Collegamento si interrompe con lo schermo spento | Tieni Chrome in primo piano e lo schermo acceso. Android può sospendere i dispositivi o la rete in background. |

La prima versione supporta IPv4 privata, un mittente e nessuna connessione
oltre la stessa LAN. Per cambiare fotocamera o risoluzione, disconnetti e
riconnetti. Una richiesta approvata ha 60 secondi per negoziare e un flusso
si chiude al più tardi dopo otto ore. Una LAN ostile può interrompere o
bloccare il traffico.

## Test

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
node --check web/app.js
shellcheck install.sh uninstall.sh
```

I test Python coprono avvio e arresto, approvazione e rifiuto, isolamento
delle sessioni, CORS, scadenza, periferiche mancanti e negoziazione WebRTC
reale con flussi sintetici. I test ICE richiedono un'interfaccia IPv4 privata.
Per un controllo reale del percorso audio, a ricevitore fermo nella sessione
PipeWire esegui `.venv/bin/python -m tests.pipewire_smoke`. La prova genera
un tono e lo legge dalla sorgente senza salvarlo.

### Verifiche del prototipo

Su Mint 22.3 sono stati verificati i dialoghi GTK di Zenity, il passaggio
di un tono sintetico nella sorgente PipeWire 1.0.5 e lo scambio
audio/video WebRTC da Chrome desktop 158. La prova Chrome ha usato una
pagina HTTPS di test, il permesso LNA concesso nel profilo temporaneo,
dispositivi sintetici e la selezione della fotocamera adattata a Linux.
In una VM con v4l2loopback 0.12.7 e il kernel di questa macchina,
`v4l2-ctl` ha letto fotogrammi YUYV identici a quelli inviati da FFmpeg
a 640 × 480 e 1280 × 720. Installazione e rimozione utente sono state
provate in una directory temporanea con controlli V4L2 e chiamate systemd
sostituiti. Manca il collaudo con Chrome Android su un telefono fisico,
GitHub Pages e l'intera catena attiva nello stesso test.

## Disinstallare

```sh
cd "$HOME/sviluppo/Progetti/phonecam"
./uninstall.sh --system
```

Senza `--system` rimuovi il servizio utente, la voce di avvio automatico e
i file del ricevitore. Usa `--system` per rimuovere anche la configurazione
webcam creata dallo script. Il modulo già caricato rimane disponibile fino
al riavvio. I pacchetti di sistema restano installati.

## Licenza

GPL-3.0-or-later. Vedi [LICENSE](LICENSE).
