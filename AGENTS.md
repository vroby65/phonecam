# PhoneCam

PhoneCam trasmette da Chrome Android a un ricevitore Python per Linux Mint
22.x. Mantieni la pagina statica, l'autorizzazione sul desktop e la trasmissione
WebRTC sulla rete locale. I comandi di sviluppo si eseguono dalla radice del
progetto.

## Dove intervenire

- `web/` contiene tutta la pagina pubblicata da `.github/workflows/pages.yml`.
  Usa JavaScript del browser, CSS e HTML senza build. Mantieni i testi
  dell'interfaccia e il README in italiano.
- `server/main.py` è l'entry point `python -m server.main`. Le risposte HTTP,
  il CORS, la macchina a stati e l'associazione HMAC sono in
  `server/signaling.py`. L'autorizzazione interattiva è in
  `server/authorization.py`.
- `server/video.py`, `server/audio.py` e `server/process.py` gestiscono FFmpeg,
  PipeWire e il ciclo di vita dei processi. Conserva i nomi **PhoneCam Camera**
  e **PhoneCam Microphone**.
- `install.sh`, `uninstall.sh` e `systemd/` coprono installazione, rimozione e
  avvio alla sessione grafica. I privilegi di amministrazione vanno richiesti
  esplicitamente usando `--system`.

## Invarianti per le modifiche

- La pagina acquisisce i media in un contesto sicuro, dopo approvazione e
  conferma autenticata. Il microfono richiede un consenso esplicito.
- Il codice casuale di associazione rimane sul desktop e nel browser. Offerta,
  risposta e chiusura sono legate alla sessione con HMAC. Un solo slot è attivo.
- Mantieni la verifica dell'origine e dell'IPv4 e conserva la verifica del
  fingerprint DTLS attraverso l'autenticazione di entrambe le descrizioni SDP.
- Mantieni ICE senza server pubblici e i processi media con argomenti fissi e
  nessuna shell. Arresto, errore e timeout devono liberare i processi figli.
- Gli script devono lasciare in pace moduli, nodi audio e servizi estranei.
  Aggiorna il README quando cambiano i passaggi operativi, il protocollo,
  l'installazione o i comandi di test.

## Verifica

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
node --check web/app.js
bash -n install.sh
bash -n uninstall.sh
shellcheck install.sh uninstall.sh
```

La suite scambia flussi WebRTC sintetici su un'interfaccia IPv4 privata. Le
uscite nei test automatici si possono sostituire per provare il teardown.
`.venv/bin/python -m tests.pipewire_smoke` verifica la sorgente PipeWire con un
tono sintetico e richiede che il demone PhoneCam sia fermo. Dopo modifiche
all'integrazione, prova la procedura del README su Chrome Android con una LAN
e un PC di test. Distingui nei risultati i test del protocollo dalle prove con
browser e periferiche reali.
