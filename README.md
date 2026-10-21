# Automatic Egg Incubator — Python/HTML GUI

Controllo di un'incubatrice automatica per uova: firmware Arduino Mega lato macchina
+ backend Python (Flask / Flask-SocketIO) con dashboard web, al posto della vecchia GUI
desktop PyQt5.

## Architettura

- **`arduinoEggIncubator/arduinoEggIncubator.ino`** — firmware Arduino Mega: lettura
  sensori (temperature, umidità, cella di carico), pilotaggio attuatori (riscaldatore,
  umidificatore, elettrovalvola acqua, ventole, motore girauova), finecorsa, e la
  torretta di segnalazione (vedi sotto).
- **`eggsIncubatorMVVM.py`** — backend Python: `SerialThread` (comunicazione seriale
  con Arduino), `MainSoftwareThread` (logica di controllo, PID/isteresi, statistiche,
  parametri persistenti), e un server Flask + Flask-SocketIO (`WebBridge`) che espone
  tutto alla dashboard web.
- **`templates/index.html`** — dashboard web (Bootstrap + Socket.IO + Plotly), con
  4 temi colore, selezione lingua IT/EN e pagine per dashboard, grafici live, storico,
  statistiche, controllo temperatura/acqua/motore e impostazioni.

## Protocollo seriale Arduino ↔ Python

I due lati comunicano con un framing testuale: `@<TAG1,val1><TAG2,val2,uid>...#`.
Entrambi i lati parsano il frame in modo generico — aggiungere un nuovo comando non
richiede toccare il parsing/framing, solo la gestione del singolo tag.

- **Python → Arduino (comandi):** la lista `command_tags` decide se un comando
  richiede ACK: ai tag presenti viene aggiunto un `uid` (esadecimale) e
  `SerialThread` ne attende la conferma, con retry automatico su timeout
  (`ack_timeout`, `max_retries`). I tag assenti dalla lista (es. `ALIVE`) sono
  fire-and-forget.
- **Arduino → Python (telemetria):** i token in uscita sono accodati con
  `queueOutgoing("<TAG,%s>", value)`; la lista `identifiers` lato Python decide
  quali tag in ingresso vengono raccolti come dati di telemetria (temperature,
  umidità, peso acqua, finecorsa, ecc.).
- Limiti di buffer lato Arduino (niente `String`, solo buffer a dimensione fissa,
  per evitare frammentazione dell'heap): `TAG_LEN=10`, `VALUE_LEN=16`,
  `UID_LEN=12`, `OUTGOING_ITEM_LEN=24`, `INCOMING_CMD_LEN=32`.

## Torretta di segnalazione (luci + buzzer)

La macchina ha una torretta a 3 luci (verde/arancione/rossa) + un buzzer, pilotata dal
comando seriale **`SIG01`**.

**Classi di messaggio** (una luce ciascuna):

| Classe            | Luce      | Buzzer |
|-------------------|-----------|--------|
| `NORMAL`          | verde     | spento |
| `WARNING`         | arancione | spento |
| `ALARM`           | rossa     | **acceso** |

**Pattern disponibili** per pilotare luci/buzzer, calcolati dal tempo assoluto
trascorso (`millis()`), quindi indipendenti dalla frequenza di polling del loop:

- **N1 – `PATTERN_SLOW_BLINK`**: 1 s ON / 1 s OFF
- **N2 – `PATTERN_TRIPLE_FLASH`**: 3 lampeggi veloci in 0.5 s, poi pausa di 0.8 s
- **N3 – `PATTERN_SOLID`**: fisso ON

Il mapping luce → pattern (`lightPatternMap[]`, `buzzerPatternForAlarm` in
`arduinoEggIncubator.ino`) è una scelta fatta in fase di inizializzazione/flash del
firmware, non configurabile a runtime: per cambiarlo va rifatto il flash della scheda.

All'avvio Arduino si porta da solo in stato `SIG_NORMAL` (verde) già in `setup()`,
prima ancora che il Python si connetta; il backend invia poi esplicitamente un
comando `SIG01 NORMAL` di conferma appena l'inizializzazione è completata.

### API Python

Sul `MainSoftwareThread`:

```python
self.signal_normal_operation(testo=None)
self.signal_warning(testo=None)
self.signal_alarm(testo=None)
```

Ciascun metodo invia sempre il comando `SIG01` ad Arduino (con ACK). Il parametro
opzionale `testo`, se valorizzato, mostra **anche** un messaggio nella dashboard web
(sezione "Machine Messages"); se omesso, non succede nient'altro.

## Dashboard — sezione "Machine Messages"

Nella pagina Dashboard è presente una lista dinamica di messaggi, popolata in tempo
reale via Socket.IO (`update_machine_message` → evento `machine_message`):

- ogni riga è colorata con lo sfondo/bordo dello stesso colore della classe di
  segnalazione (verde/arancione/rosso);
- ogni messaggio ha una **✕** per rimuoverlo dalla visualizzazione (solo lato
  browser corrente, non modifica lo storico condiviso);
- un pulsante **Reset all** svuota la lista visualizzata (anch'esso solo lato
  browser);
- lo storico (ultimi 100 messaggi) viene mantenuto server-side e reinviato ai nuovi
  browser che si collegano tramite `full_state`, quindi un refresh della pagina può
  far riapparire messaggi già rimossi in precedenza.
