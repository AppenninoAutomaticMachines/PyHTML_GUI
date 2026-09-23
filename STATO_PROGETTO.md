# Stato progetto – Incubatrice Automatica
Aggiornato: 2026-09-21

---

## Cosa è stato fatto (in ordine cronologico)

### 1. Migrazione GUI PyQt5 → Interfaccia Web

La vecchia GUI (`eggsIncubatorGUI.ui` / `eggsIncubatorGUI.py`) è stata sostituita
con un server Flask+SocketIO che serve una pagina HTML accessibile dal browser.

**File modificati:**
- `eggsIncubatorMVVM.py` – aggiunto Flask, SocketIO, classe `WebBridge`, nuovo `__main__`
- `templates/index.html` – nuova interfaccia web (creata da zero)

**Perché PyQt5 rimane:**
`SerialThread` e `MainSoftwareThread` usano ancora `QThread`/`pyqtSignal`.
Si usa `QCoreApplication` (headless, senza display) solo per l'event loop Qt.

**Avvio sul Raspberry Pi:**
```bash
python3 eggsIncubatorMVVM.py
# → server su http://0.0.0.0:5000
```

**Dipendenze da installare (una volta sola):**
```bash
pip install flask flask-socketio
```

**Accesso remoto:** installare Tailscale sul Raspberry Pi e sul PC/telefono.
Nessun port-forwarding necessario. L'IP Tailscale del Pi è fisso.

---

### 2. Persistenza parametri

Funziona esattamente come prima:
- All'avvio `MainSoftwareThread` legge `Parameters/parameters.json`
- I valori vengono inviati al browser tramite i signal Qt → WebBridge → SocketIO
- Il browser riceve tutto via evento `full_state` alla connessione
- I nuovi browser che si collegano dopo l'avvio ricevono lo stato corrente dalla cache `current_state`

---

### 3. Live Chart (pagina "Live Chart")

Grafico Plotly.js interattivo con T1, T2, T3, T4, Temperatura Esterna e Setpoint
(quest'ultimo tratteggiato: è un riferimento di controllo, non una misura).

**Funzionamento:**
- All'avvio legge gli **ultimi 4 h di CSV** da `Machine_Statistics/Temperatures/` e
  `Machine_Statistics/External_Temperature/` → il grafico si popola subito con dati reali
- Durante il funzionamento aggiunge un punto ogni 10 secondi
- I nuovi browser ricevono tutta la storia disponibile alla connessione
- Crosshair/cursore: hovering mostra X (ora) e Y (°C) per tutte le tracce
- Range buttons: 30 min / 1 h / 2 h / 4 h / All

**Filtro valori DS18B20 invalidi:**
Valori −127 °C, 85 °C o fuori range 5–80 °C → mostrati come gap nel grafico (non come picchi).

---

### 4. Redesign interfaccia – Dark Dashboard

`templates/index.html` riscritto completamente con:

- **Tema dark** caldo: sfondo `#0e0c08`, card `#221d12`, accento ambra. Da qui in poi
  quei colori non sono più scritti nelle regole ma sono variabili del tema `dark`
  (vedi punto 6): l'aspetto è lo stesso, la definizione sta in un posto solo
- **Sidebar fissa** a sinistra con le pagine di navigazione (ora 10, vedi elenco sotto)
- **Pagina Dashboard** nuova: tile grandi con T1–T4+Esterna, status pills heater/umidificatore/valvola,
  KPI secondari, barra avanzamento incubazione
- **Pagina Temperature**: controlli heater mode, algoritmo (Hysteresis/PID), parametri
- **Pagina Water & Humidity**: letture, modo umidificatore, isteresi, elettrovalvola
- **Pagina Motor**: status + controlli manuali (CCW/CW/Force Turn/Lay Horizontal)
- **Pagina Incubation**: data inizio, durata, barra progresso con % completamento
- **Pagina Statistics**: tabelle min/mean/max, contatori, reset, plot buttons
- **Pagina Live Chart**: grafico Plotly full-width
- **Pagina History**: grafici storici da CSV (dataset + modalità today/all/mean)
- **Pagina System**: stato connettività, sensori, attuatori, ciclo di controllo
- **Pagina Appearance**: scelta del tema e della lingua (vedi punto 6)

**Compatibile mobile:** sidebar si nasconde su schermi piccoli (hamburger in alto a sinistra).

---

### 5. Regolazioni rapide dalla Dashboard

Dalla dashboard si regolano ora i parametri principali senza cambiare pagina. Ogni
controllo usa **lo stesso nome di spinbox** della pagina di controllo corrispondente,
quindi i due campi sono lo stesso parametro e si riallineano da soli tramite l'eco
`update_spinbox` del server.

| Controllo in dashboard | Spinbox | Pagina gemella |
|---|---|---|
| Set Point temperatura | `setPointTemperature_PID_spinBox` | Temperature → PID Parameters |
| Set Point umidità     | `setPointHumidity_spinBox`        | Water & Humidity → Humidity Hysteresis |
| Max / Min livello acqua | `max/minHysteresisValue_waterLevelControl_spinBox` | Water & Humidity → Water Level Hysteresis |

**Setpoint umidità — scelta di progetto.** L'umidità è regolata da un controllore a
isteresi, che non ha un setpoint proprio: il setpoint è definito come il **centro della
banda min/max** (`MainSoftwareThread.get_humidity_setpoint`). Spostandolo la banda
trasla mantenendo la sua ampiezza (`apply_humidity_setpoint`); modificando i limiti il
setpoint viene ricalcolato e ripubblicato (`publish_humidity_setpoint`). Essendo
derivato non viene salvato in `parameters.json`: le uniche fonti restano
`HUMIDITY_HYSTERESIS_CONTROLLER_UPPER/LOWER_LIMIT`, così i due valori non possono
divergere.

**Lato browser:** un parametro può comparire su più pagine, quindi gli `<input>` sono
marcati `data-sb="<nome spinbox>"` e `applySpinbox()` aggiorna tutte le occorrenze
(saltando quella in cui si sta digitando). I bottoni `±` passano da `bumpSpinbox()`.

**Allineamento:** i due Set Point sono l'ultima riga della rispettiva card e usano
`.sp-ctl.at-bottom` (`margin-top: auto`), così restano sulla stessa orizzontale anche se
il contenuto sopra ha altezze diverse. Da qui anche il `mt-auto` sulla riga KPI della
card umidità: spartisce lo spazio libero invece di concentrarlo in un unico stacco.

**Regola per le tre card della riga 1:** sono `d-flex flex-column justify-content-center`,
quindi ognuna deve avere **almeno un elemento con margine automatico** (`mt-auto`, o
`.sp-ctl.at-bottom`) che si prenda lo spazio libero. Senza, il contenuto viene centrato
in verticale e il `gcard-title` scivola sotto la linea dei titoli vicini: era il caso di
*Water Reservoir*, risolto con `mt-auto pt-3` sul blocco dei limiti Max/Min Level.

**Indicatori grafici aggiunti:** tacca del setpoint + `Δ SP` sul gauge umidità (come già
sul gauge temperatura) e linee orizzontali MAX (verde) / MIN (rosso) sul disegno del
serbatoio. Geometria del serbatoio centralizzata in `TANK_MAX_KG / TANK_TOP / TANK_H`
e `tankY(kg)`, usata sia dal riempimento sia dalle linee. Corretta l'etichetta della
scala del serbatoio: la prima tacca è a **3 kg**, non 4 kg (y=36 su scala 0–4 kg).

**Gauge umidità allineato a quello della temperatura:** stessa geometria (viewBox
`0 0 180 150`, `max-width:220px`, centro 90/90, r=62, spessore 10, testi alle stesse
y) e stesse costanti d'arco — `HUM_CIRC/HUM_ARC` ora derivano da `TEMP_CIRC/TEMP_ARC`.
Sparito il pallino che si vedeva all'inizio della scala: con arco di lunghezza 0 il
`stroke-linecap="round"` disegna comunque un punto, quindi `updateHumGauge` mette
`stroke:transparent` finché `dash === 0` e lo rimette a stringa vuota appena c'è arco
(stringa vuota, non un colore letterale, per non congelare la tinta al cambio tema).

---

### 6. Temi selezionabili (dark / light / midnight)

Nuova pagina **Appearance** (sidebar → Setup, icona 🎨) con tre temi. La scelta sta in
`localStorage['incubatorTheme']`, quindi è per browser e sopravvive al ricaricamento.
Sulla stessa pagina ci sono anche i due bottoni di lingua (la barretta con le bandiere
in alto a destra resta dov'era).

| Tema | Aspetto |
|---|---|
| `dark` | quello storico, default: fondo caldo, accento ambra |
| `light` | fondo avorio, card bianche, accenti scuriti per il contrasto |
| `midnight` | scuro freddo blu-grigio, accento turchese |
| `meadow` | vicino a `light` (fondo chiaro, card bianche, ombra leggera) ma tinta verde: accento pino (`--amber: #2e7d4f`), `--green` resta oliva/lime per non confondersi con l'accento |

**Come è fatto:**
- Tutti i colori sono variabili CSS dichiarate tre volte: `html[data-theme="dark"]`,
  `"light"`, `"midnight"`. Nessuna regola del foglio di stile contiene più un colore
  scritto a mano: cambiare tema = cambiare `data-theme` su `<html>`. `--amber` è il
  nome storico dell'**accento primario**, non per forza ambra (in midnight è turchese).
- Oltre ai colori base ci sono i token derivati (`--hairline`, `--accent-soft`,
  `--heat-bg`, `--red-bd`, `--water-fill`, `--on-accent`…): servivano perché prima le
  tinte traslucide erano `rgba()` cablate, che su fondo chiaro non funzionano.
- Uno script inline nel `<head>` applica il tema **prima del primo disegno**: messo in
  fondo alla pagina si vedrebbe un lampo del tema sbagliato a ogni ricarica.
- Gli SVG statici (gauge, serbatoio, bussola) usano le classi `.svg-muted`,
  `.svg-track`, `.svg-ref`… invece degli attributi `fill=`/`stroke=`: gli attributi di
  presentazione perdono contro qualsiasi regola CSS, quindi seguono il tema da soli.
- Dove invece è il JS a decidere il colore (arco del gauge secondo la temperatura,
  frecce del motore) si scrive su `element.style`, che vince sulle classi; i valori si
  leggono dalle variabili con `cssVar('--xxx')`. Per questo `updateTempGauge` e
  `updateMotorCompass` ricordano l'ultimo valore disegnato in `_lastAvg` /
  `_lastRotState`, e il Δ del gauge è stato estratto in `renderTempDelta()`.
- Plotly disegna su canvas e non vede il CSS: `chartPalette()` gli passa i colori del
  tema attivo, e `repaintThemedGraphics()` (chiamata da `setTheme`) ridisegna gauge,
  bussola e grafici — senza, resterebbero della tinta precedente fino al prossimo dato.
- Le card del selettore mostrano l'anteprima dei colori di *quel* tema: il campione ha
  `data-preview="<nome>"` e quel selettore è aggiunto al blocco di variabili del tema,
  così l'anteprima non può andare fuori sincrono con il tema vero.

**Aggiungere un tema:** copiare un blocco di variabili con il nuovo `data-theme`,
aggiungere il nome a `THEMES`, aggiungere la card nella pagina Appearance. Nient'altro.

**Differenza voluta:** il segnaposto `--.-` del gauge, che prima usava il colore del
bordo (quasi invisibile), ora usa `--text`.

**Verifica:** l'app gira sul Pi, non su questa macchina. Controllato con un harness
usa-e-getta (copia della pagina senza CDN + stub di `io`/`Plotly`, Edge headless):
i due blocchi `<script>` passano `node --check`, e dashboard / temperatura / motore /
Appearance sono stati resi nei tre temi. Il tema dark è identico a prima.

---

## Struttura cartelle dati (create automaticamente a runtime)

```
AutomaticEggIncubator/
├── eggsIncubatorMVVM.py         ← programma principale
├── templates/
│   └── index.html               ← interfaccia web
├── Parameters/
│   ├── parameters.json          ← parametri salvati (JSON)
│   └── parameters.json.bak      ← backup automatico
├── Machine_Statistics/          ← CSV giornalieri (YYYY-MM-DD.csv)
│   ├── Temperatures/            → Timestamp, TMP01..TMP04, SETPOINT, SP_MIN, SP_MAX
│   │                              (SETPOINT = riferimento PID, SP_MIN/SP_MAX = banda
│   │                               isteresi; l'header dei file già esistenti viene
│   │                               migrato in automatico aggiungendo le colonne)
│   ├── External_Temperature/    → Timestamp, EXTT
│   ├── Humidity/
│   ├── Heater/
│   ├── Humidifier/
│   ├── Water_Weight/
│   ├── PID_Duty_Cycle/
│   └── General_Purpose/
├── MainSoftwareThread_Log/      ← log giornaliero
└── SerialThread_Log/            ← log comunicazione seriale
```

---

## RISOLTO: reset dell'Arduino su `HTR01, True` — sketch da Mega girato su UNO

**Sintomo:** `⚠️ NO ACK for command: @<HTR01, True, ...>#` e ~2.44 s di silenzio sulla
seriale dopo ogni comando. 39 comandi `HTR01, True` → 38 NO ACK; zero fallimenti su
`HTR01, False`, `PWM01`, `ELV01`.

**Causa:** lo sketch è scritto per un **Arduino MEGA** e usa i pin 22..45 per relè,
induttori e segnalazioni. Girando su un **UNO** (`NUM_DIGITAL_PINS` 20 contro 70 del
Mega) quei pin non esistono, e `digitalWrite()` del core AVR **non controlla il range**
(`wiring_digital.c`): indicizza tabelle PROGMEM dimensionate su `NUM_DIGITAL_PINS`,
ricava un puntatore arbitrario e ci scrive dentro.

L'asimmetria True/False viene da lì:

```c
if (val == LOW) { *out &= ~bit; }   // azzera un bit: sulla stessa locazione di solito e' un no-op
else            { *out |= bit;  }   // ACCENDE un bit in una locazione a caso -> memoria corrotta
```

`HEATER_PIN` è 37 → `digitalWrite(37, HIGH)` accende un bit in un indirizzo arbitrario
(registro di I/O, stack pointer, variabili) e la scheda va in crash e riparte. Coerente
con tutto il resto: i riavvii arrivano a decine al secondo (troppo veloci per un reset
hardware col bootloader: è lo sketch che salta al vettore di reset), `PWM01` usa il pin
13 che esiste anche su UNO e non ha mai dato problemi, e `HUMER01`/`ELV01` non erano mai
stati inviati con valore `True`.

**Correzione:** wrapper `safePinMode` / `safeDigitalWrite` / `safeDigitalRead` /
`safeAnalogWrite` in cima allo sketch, che ignorano i pin oltre `NUM_DIGITAL_PINS`, più
macro che dirottano tutte le chiamate. Su UNO i pin inesistenti diventano inerti (la
simulazione gira senza danni), sul Mega il comportamento è identico a prima. C'è anche un
`#warning` a compile time se la scheda ha meno di 46 pin.

**Conseguenza collaterale che era emersa:** `u_cmd` è una globale azzerata dal riavvio, e
il PC inviava `PWM01` solo alla variazione. Con il PID saturo a 1.0 il comando non
cambiava mai → dopo un riavvio il riscaldatore restava spento mentre l'interfaccia
mostrava duty 100 %. Misurato: 6 riavvii consecutivi senza reinvio, ~2.5 minuti di
riscaldatore spento.

**Robustezza aggiunta lungo la diagnosi** (utile a prescindere dalla causa):
1. `HTR01` non viene più inviato quando il PID è attivo: lì scalda l'SSR e il relè è
   ridondante. Nato come aggiramento, ora è una scelta di progetto — reversibile
2. `PWM01` viene rinviato ogni 5 s anche a valore invariato, e il firmware azzera il duty
   se non riceve comandi per `FAILSAFE_MS` (30 s)
3. Arduino stampa `@<BOOT, 1>#` in `setup()`; il PC lo intercetta
   (`SerialThread.board_reset_detected` → `MainSoftwareThread.handle_board_reset`) e
   rimanda subito lo stato di tutti gli attuatori. Il riallineamento è limitato a uno ogni
   5 s e dopo 3 riavvii in 30 s smette di ripristinare il relè: senza questo freno il
   ripristino rimandava proprio il comando che faceva riavviare la scheda, creando un
   ciclo di riavvii a decine al secondo
4. Il frame periodico porta `<UPT, millis>` e `<MEM, RAM libera>`: il PC distingue da solo
   riavvio (uptime che torna indietro), stallo del firmware (uptime continuo con un salto)
   e problema lato PC (uptime regolare ma nessun dato). Diagnostica rimovibile

---

## RISOLTO: Chromium bloccato in loading sul Raspberry — popup gnome-keyring

All'avvio automatico del browser (`_open_browser()` in fondo a `eggsIncubatorMVVM.py`)
Chromium apriva il popup *"Choose password for new keyring"* e restava in caricamento
senza mostrare la pagina. Chromium cerca un portachiavi di sistema per salvare le
password; sul Pi con autologin il keyring non esiste/non è sbloccato e il processo si
ferma sul dialogo.

Soluzione: due flag nel `subprocess.Popen` del browser

- `--password-store=basic` → Chromium usa lo store interno, niente gnome-keyring/kwallet
- `--use-mock-keychain` → stessa cosa lato macOS/keychain, innocuo su Linux

Se il popup dovesse ricomparire, sul Pi cancellare il keyring mai completato:
`rm -rf ~/.local/share/keyrings/` (nessuna password salvata da perdere in questo progetto).

---

## RISOLTO: linee dei limiti nel serbatoio ferme quando si muovono max/min

Nella card *Water Reservoir* della dashboard le due linee orizzontali non seguivano lo
spinbox. Le linee vengono ridisegnate da `applySpinbox()` (`templates/index.html`), che
gira **solo** sull'evento `update_spinbox` in arrivo dal server: la modifica dal browser
parte come `spinbox_change` e non torna indietro da sola.

`handle_float_spinBox_value` emetteva l'eco dei limiti acqua solo nel ramo in cui i due
limiti collassano (max ≤ min, dove deve trascinare anche l'altro); nel caso normale non
emetteva nulla. Ora l'eco viene emessa in tutti i casi, come già si faceva per
`setPointTemperature_PID_spinBox`.

Effetto collaterale risolto: anche `WebBridge.current_state['spinboxes']` si aggiorna solo
tramite quell'eco, quindi un browser che si ricollegava riceveva in `full_state` il valore
vecchio letto da `parameters.json`.

Nota: gli spinbox di isteresi **temperatura** hanno ancora lo stesso buco (eco solo nel
ramo di collasso). Non dà fastidio oggi perché nessun disegno dipende da quei due valori,
ma il `full_state` di un browser che si ricollega resta disallineato.

---

## Librerie front-end servite da `static/`, non da CDN

`templates/index.html` caricava Bootstrap, Socket.IO e Plotly dai rispettivi CDN. Sul
Raspberry, che può stare su una rete senza uscita verso internet, il risultato è una
pagina che si apre ma resta muta: senza `socket.io.min.js` la variabile `io` non esiste,
`const socket = io()` solleva un `ReferenceError` e lo script principale si ferma lì —
nessun dato, nessun grafico, indicatore di connessione fermo su *Disconnected*.

In `static/` ci sono i file locali, con le stesse versioni che erano sui CDN:
Bootstrap 5.3.2 (`bootstrap.min.css`, `bootstrap.bundle.min.js`), Socket.IO 4.7.2
(`socket.io.min.js`), Plotly 2.32.0 (`plotly-2.32.0.min.js`). Il template li referenzia
con `url_for('static', ...)`. Aggiornando una libreria va sostituito il file, non l'URL.

(`flatpickr.min.js` / `flatpickr-dark.min.css` sono in `static/` ma al momento non usati
dalla pagina.)

---

## Prestazioni sul Raspberry Pi 3: modalità leggera (`?lite=1`)

Sul Pi 3 a 1080p la dashboard era inusabile: `htop` mostrava il processo Chromium
`--type=renderer` stabilmente sopra il 50 % di CPU. Ridurre la finestra a 1280×720 non ha
cambiato quasi nulla → il costo non erano i pixel, era il contenuto della pagina.

Tre interventi in `templates/index.html`:

1. **Plotly caricato su richiesta.** Erano 3,6 MB di JS scaricati e interpretati a ogni
   ricarica, più decine di MB di RAM, anche restando in Dashboard — su 1 GB di RAM questo
   da solo poteva mandare il Pi in swap sulla microSD. Ora lo carica `ensurePlotly()`,
   chiamata solo all'apertura di Live Chart o History.
2. **Grafico live costruito alla prima apertura della pagina, non al `connect`.** Prima,
   appena arrivava `chart_history`, finivano nel DOM fino a 1440 punti × 6 tracce in SVG,
   ridisegnati a ogni campione anche mentre si guardava altro. Ora i punti si accumulano
   in `_chartPending` (stesso tetto del grafico) e il grafico nasce quando serve.
3. **Modalità leggera.** Attiva con `?lite=1` (memorizzata in `localStorage`, si annulla
   con `?lite=0`). Disattiva le animazioni CSS `infinite` — un pallino che lampeggia
   obbliga Chromium a ricomporre la pagina a ogni frame, e dove si rasterizza in software
   costa CPU di continuo — le ombre e i `drop-shadow`; nel grafico mette `hovermode:
   'closest'` al posto di `'x unified'`, toglie gli spikes (facevano ridisegnare tutte le
   tracce a ogni movimento del mouse) e tiene 360 punti invece di 1440.

`_open_browser()` apre `?lite=1` quando `sys.platform` è Linux, cioè per il browser che
parte sul Pi stesso. Da un PC in rete (`http://<ip-del-pi>:5000`) resta la versione piena.

**Override manuale:** in cima a `eggsIncubatorMVVM.py`, vicino a `portSetup`, c'è
`BROWSER_LITE_MODE` (`None` = scelta automatica per sistema operativo, `True`/`False` =
forza sempre lite/piena a prescindere da dove gira). Utile per testare la pagina piena
sul Pi o la leggera da PC senza toccare `?lite=` a mano ogni volta.

**Resta la cosa più efficace di tutte:** non aprire il browser sul Pi. Il server ascolta su
`0.0.0.0:5000`, quindi la dashboard si può guardare da un PC o un tablet, lasciando al Pi
il solo lavoro di controllo — che è leggero.

**Verifica:** harness usa-e-getta (copia della pagina con i `url_for` risolti + `static/`,
Edge headless `--dump-dom`, script iniettato che riporta lo stato nel DOM). Dashboard:
nessun errore JS, Plotly **non** caricato. Dopo il click su Live Chart: Plotly caricato,
`chartInited` vero, SVG presente, nessun errore. Con `?lite=1`: `data-lite` sull'`html`,
`hovermode` `closest`, spikes disattivati, 360 punti.

### Bug: pulsanti 2h/4h del Live Chart vuoti a metà in modalità leggera

**Sintomo segnalato:** con il range "2 h" selezionato, il grafico disegna l'asse da
-2h a ora ma i dati compaiono solo nell'ultima ora (metà asse vuoto). "30 min" e "1 h"
invece funzionano.

**Causa:** `CHART_MAX_POINTS` (punto 3 sopra) è 360 in modalità leggera = 1 h di storico
a un campione ogni 10 s, non 1440 = 4 h. `setChartRange()` calcola comunque l'asse X
in base ai minuti richiesti dal pulsante, senza sapere quanto storico è davvero rimasto
nel buffer JS (`_chartPending`/tracce già disegnate): per "2 h" e "4 h" chiede una
finestra più larga di quella disponibile. Dato che `_open_browser()` apre il browser sul
Pi proprio con `?lite=1`, chi guarda il Live Chart *sul Pi* vede sempre questo effetto.

**Correzione:** in `templates/index.html`, se `LITE` è attivo i pulsanti `#chartRange2h`
e `#chartRange4h` vengono disabilitati (`btn.disabled = true` + tooltip) invece di
restare cliccabili con un risultato fuorviante. "30 min", "1 h" e "All" restano invariati
(sempre dentro il tetto di 1 h, oppure autorange sul dato che c'è davvero). La storia
oltre 1 h resta comunque consultabile dalla pagina History, che legge dai CSV e non ha
questo limite.

**Verifica:** non provato a runtime sul Pi (nessun Python nel PATH su questa macchina).

---

### Secondo giro: come si scrive nel DOM

Tolto Plotly dalla Dashboard, il renderer restava comunque sopra il 50 % di CPU. Il frame
dall'Arduino arriva circa una volta al secondo (parte solo con `gotTemperatures`), quindi
non era la frequenza: era il costo di ogni singolo aggiornamento.

- **`cssVar()` chiamava `getComputedStyle` a ogni uso.** Chiamata dopo una scrittura nel
  DOM, quella funzione obbliga il browser a ricalcolare stile e layout *subito*, in mezzo
  all'aggiornamento (forced synchronous layout). Gauge e bussola alternavano le due cose
  a ogni dato: si pagava più volte al secondo il layout dell'intera pagina. Ora c'è una
  cache, svuotata in `setTheme()`.
- **`setPill()` rifaceva l'`innerHTML`** a ogni chiamata, per sei badge: il browser
  buttava via pallino e testo e li ricostruiva, parsing HTML incluso. Ora la struttura si
  crea una volta (`el.__pillLabel`) e si aggiorna solo il nodo di testo.
- **Si scriveva sempre, anche valori identici.** `setText`, `setHealthItem`, le classi dei
  pallini, le barre, gli attributi SVG di gauge e serbatoio: un valore uguale a quello già
  presente marca comunque il nodo come sporco e fa rifare stile, layout e paint di quella
  zona. Ora tutte queste scritture passano da un confronto (`setAttr`, `setStyleOnce`, o
  un `if` esplicito). Per gli stili il confronto non può essere con `el.style.<prop>`: il
  browser normalizza i colori (`#ffcc00` → `rgb(255, 204, 0)`), quindi `setStyleOnce`
  ricorda l'ultimo valore applicato sull'elemento stesso.
- In modalità leggera, `contain: layout style` su `.gcard`/`.kpi`/`.temp-tile`: un numero
  che cambia dentro una card non propaga il ricalcolo a tutta la pagina. Non si usa
  `contain: paint`, che ritaglierebbe ciò che sporge dal riquadro.

**Verifica:** harness con stub di `io()` che spara due `update_view` di seguito con valori
diversi, più `update_spinbox` e `update_motor` (le guardie "scrivi solo se cambia" non
devono bloccare gli aggiornamenti veri). In entrambe le modalità i valori arrivano a
schermo: gauge, Δ setpoint, serbatoio e linee dei limiti, barra del duty cycle, i sei
badge con il loro pallino, etichetta del motore. Nessun errore JS.

### Terzo giro: si aggiorna solo la pagina visibile

Misura decisiva: fermando il solo Python (`pkill -f eggsIncubatorMVVM.py`, così Chromium
resta aperto — con `Ctrl+C` il SIGINT va a tutto il gruppo di processi e chiude anche il
browser) il carico del renderer sparisce. Quindi non c'è nulla che animi di continuo: il
costo è tutto nell'arrivo dei dati.

`applyView()` scriveva una sessantina di elementi per ogni dato, sparsi su Dashboard,
Temperature, Water, System — anche sulle pagine nascoste. Ora `setText`, `setPill`,
`setHealthItem`, `setAttr` e `setStyleOnce` controllano a quale `.page` appartiene
l'elemento (`pageOf()`, risultato messo in cache sull'elemento) e se non è quella attiva
**mettono il valore da parte** invece di scriverlo. All'apertura di una pagina,
`refreshActivePage()` chiama `flushPending()` e riapplica l'ultimo `update_view` /
`update_motor` (i disegni — gauge, serbatoio, bussola — vanno ricalcolati dal dato, non
basta il singolo valore messo da parte).

La coda è stata preferita al "ricordare l'ultimo payload di ogni canale" perché diverse
scritture non passano dai quattro canali principali: la data di fine incubazione, lo stato
del broker, l'algoritmo attivo. Con la coda non serve censirle.

**Verifica:** harness che manda `update_view`, `update_statistics`, `update_days`,
`update_motor`, `update_radio`, `update_date` e `update_spinbox` mentre si è in Dashboard,
poi apre Temperature, Water, Statistics, Incubation, Motor e System e rilegge cosa c'è
scritto. Tutti i valori sono al loro posto in entrambe le modalità, nessun errore JS.

### Porta seriale per sistema

`portSetup` non è più fissa: `/dev/ttyUSB0` su Linux, `COM9` altrove
(`eggsIncubatorMVVM.py`, in cima). Prima era `COM9` e basta, quindi sul Pi andava
corretta a mano — e la correzione si perdeva a ogni copia del progetto dal PC.
Se un domani l'Arduino si presentasse come `/dev/ttyACM0` (succede con le schede
originali, mentre i cloni con CH340 usano `ttyUSB0`), è quella riga da cambiare.

### Font emoji sul Pi

Le icone della sidebar e dei titoli sono emoji nel markup, non immagini. Raspberry Pi OS
non porta un font emoji di serie, quindi senza `sudo apt install fonts-noto-color-emoji`
(poi `fc-cache -f` e riavvio di Chromium) restano quadratini vuoti. Da tenere presente
reinstallando il sistema.

### Tempi ON/OFF del relè salvati solo in modo isteresi

`process_serial_data` salvava `TEMPERATURE_HYSTERESIS_CONTROLLER_TIME_ON`/`_OFF` a ogni
frame seriale, e ogni `save_parameter` riscrive *tutto* `parameters.json`: il file era in
scrittura quasi di continuo. In modo PID quei due valori non servono nemmeno — il relè
HTR01 non viene mai comandato (scalda l'SSR via PWM), quindi contano un'accensione che
nella realtà non avviene. Ora il salvataggio avviene solo se
`pid_temperature_is_activated` è falso.

Il contatore continua comunque a girare e resta visibile nelle statistiche: cambia solo
che in modo PID non finisce su file.

### Refresh rate della pagina configurabile (System → Performance)

Il frame dall'Arduino arriva ~1 volta al secondo e finora la pagina ridisegnava
a ogni frame. Ora l'intervallo di ridisegno è un parametro: spinbox
`webRefreshInterval_spinBox` in **System → Performance**, salvato come
`WEB_REFRESH_INTERVAL_MS` in `parameters.json` (clamp 250–10000 ms lato
Python in `handle_float_spinBox_value`, default 1000).

**Scelta di progetto:** è un parametro server-side (in `parameters.json`), non
`localStorage` come tema/lingua. Vale per tutti i browser collegati e
sopravvive al riavvio del server — a differenza del tema, qui non c'è motivo
per cui due browser vogliano vedere l'incubatrice "aggiornata" a velocità
diverse.

**Lato Python:** nessun controllore fisico da avvisare — è solo un numero che
si salva e si rimanda indietro con `update_spinbox_value.emit(...)`, stesso
pattern degli altri spinbox. Arduino e `MainSoftwareThread` continuano a
lavorare alla velocità di sempre: cambia solo quanto spesso il browser
*applica* i dati che riceve.

**Lato browser:** `applySpinbox` aggiorna `_refreshIntervalMs`; i quattro
canali periodici (`update_view`, `update_motor`, `update_statistics`,
`update_days_statistics`) passano ora da `throttled(fn)`, un throttle con
trailing call — il dato più recente viene comunque applicato, al più tardi
dopo `_refreshIntervalMs` dall'ultimo rendering, quindi nessuna perdita di
informazione, solo meno ridisegni. Gli eco immediati (`update_spinbox`,
`update_date`, `update_radio`) restano non throttled: sono cambi voluti
dall'utente, non dati periodici. `refreshActivePage()` (cambio pagina) chiama
`applyView`/`applyMotor` direttamente, bypassando il throttle: aprire una
pagina mostra subito l'ultimo dato, senza aspettare il prossimo tick.

**Verifica:** `node --check` sui due blocchi `<script>` (stesso harness
usa-e-getta delle altre verifiche di questo file); non provato a runtime sul
Pi.

### Non ancora fatto (I/O su microSD)

Con il controllore a isteresi selezionato il salvataggio resta a ogni frame seriale, e
ogni messaggio di log fa open+write+close. Non è la causa della lentezza dell'interfaccia
— quella è nel browser — ma su microSD è I/O sincrono continuo. Da sistemare con una
scrittura periodica (ogni N secondi, o solo a valore cambiato) invece che a ogni frame.

---

### Nuovo parametro: velocità ventole di ricircolo aria (0-100%)

Aggiunto un controllo diretto (non calcolato da un algoritmo, a differenza del duty PID
del riscaldatore) per la velocità delle ventole di ricircolo dell'aria nell'incubatrice.

**Arduino (`arduinoEggIncubator.ino`):** il pin usato è il 7, prima `FREE_PC817_PIN`
(libero, mai inizializzato né scritto in tutto lo sketch) — rinominato `FAN_PWM_PIN` e
riusato perché è già PWM-capable sul Mega. Nuovo tag seriale `FAN01`: FLOAT 0.0-1.0,
scritto con `analogWrite()` **diretto**, senza la finestra software a 10 s usata per
l'heater (lì serve perché pilota un SSR a stato solido; qui è un normale pilotaggio PWM
di un motore/ventola, quindi il duty va applicato subito). Il pin va a riposo (0) sia nel
failsafe di silenzio seriale (>4 s, stesso blocco di HTR01/HUMER01/ELV01/HEATER_PWM) sia
in `setup()` dopo un riavvio della scheda — coerente con la sezione RISOLTO qui sotto sul
riavvio da `HTR01, True`.

**Python (`eggsIncubatorMVVM.py`):** `FAN01` aggiunto a `command_tags` (quindi passa per
ACK/uid come gli altri comandi attuatore). Parametro salvato in `parameters.json` come
`FAN_SPEED_PERCENT` (scala 0-100, la stessa unità mostrata in dashboard); verso Arduino
si manda `FAN01` con il valore diviso 100 (scala 0.0-1.0, come `PWM01`). Percorso identico
agli altri parametri: `handle_float_spinBox_value` (spinbox `fanSpeed_spinBox`) salva e
invia subito; `parameters_initialization_from_file` lo ricarica e lo rimanda ad Arduino
all'avvio del programma; `handle_board_reset` lo rimanda di nuovo dopo un riavvio della
scheda, **incondizionatamente** (a differenza di HTR01/HUMER01/ELV01, le ventole non
dipendono dalla modalità di riscaldamento PID/isteresi, quindi non c'è un ramo da cui
escluderle).

**Dashboard (`templates/index.html`):** nuova card in una riga propria, subito dopo i tre
gauge principali (temperatura, umidità+acqua, serbatoio) e prima della riga KPI. Stessa
geometria/pattern esatti degli altri tre (arco `stroke-dasharray` su `r=62`, scala
`HUM_CIRC`/`HUM_ARC` riusata come `FAN_CIRC`/`FAN_ARC` perché è la stessa 0–100%,
controllo `sp-ctl.at-bottom` con ± in fondo alla card — qui è `fanSpeed_spinBox`).
A differenza degli altri due gauge non c'è una tacca di setpoint né un Δ: è un comando
diretto, non una misura confrontata con un riferimento, quindi arco e valore coincidono
sempre con quanto impostato. `updateFanGauge()` riusa lo stesso trucco anti-pallino
dell'umidità (arco trasparente quando il dash è 0) e si aggiorna da `applySpinbox()`,
la stessa eco di tutti gli altri spinbox: nessun dato torna da Arduino per questo
valore (come il duty PID), quindi il gauge mostra ciò che il software ha
impostato/salvato, non una lettura hardware. Etichette tradotte in `I18N_IT`
("Fan Speed" → "Velocità Ventole", "Speed" → "Velocità", "recirculation fans" →
"ventole ricircolo" — quest'ultima è un `<text>` SVG, quindi va registrata a mano
nell'elenco id di `applyStaticTranslations()` insieme a `svgTempSubLabel`/`svgHumSubLabel`,
non basta la classe `.svg-muted`).

**Verifica:** `node --check` sui due blocchi `<script>` (stesso harness usa-e-getta delle
altre verifiche di questo file) — nessun errore di sintassi. Non provato a runtime
(l'app gira solo sul Pi, niente Python nel PATH su questa macchina). Da testare sul Pi:
caricare lo sketch aggiornato, verificare che lo spinbox in dashboard invii `FAN01`,
che il gauge segua il valore impostato, che le ventole rispondano al variare della
percentuale, e che un riavvio dell'Arduino (scollega/ricollega USB) ripristini da solo
l'ultima velocità impostata.

---

## RISOLTO: `WGT01` arrivava come `SGT01` (o peggio) — `String` esauriva la RAM sull'UNO di test

**Sintomo:** in simulazione su un Arduino **UNO** (2 KB di RAM, contro gli 8 KB del Mega
di produzione), il tag del peso acqua arrivava sistematicamente con il primo carattere
sbagliato: `WGT01` diventava `SGT01`. Rinominandolo a mano in `WWX01` per isolare il
problema, arrivava `SWX01` — sempre e solo il primo carattere corrotto, sempre con la
stessa `S`, qualunque fosse il nome del tag. Lato Python il tag corrotto non combaciava
con nessun prefisso di `identifiers` (`eggsIncubatorMVVM.py`), quindi il dato veniva
scartato in silenzio: `current_weight` restava vuoto e scattava il warning "Pacchetto
seriale incompleto... Water_Weight".

**Causa:** lo sketch usava `String` per tutto il percorso seriale — `listofDataToSend[20]`
e `receivedCommands[20]` erano array di `String`, più `String tag/value/uid/pendingACK`
temporanee create a ogni comando ricevuto. Su AVR `String` alloca/libera sull'heap in
continuazione; con solo ~490 byte liberi (misurati via `<MEM,...>`) su un UNO l'heap si
frammenta al punto che un blocco appena liberato viene riassegnato senza essere
riscritto per intero, lasciando il **primo byte** del vecchio contenuto (con ottima
probabilità l'inizio di `STPR01`, il tag motore che passa dallo stesso meccanismo) al
posto del primo byte del nuovo. Da cui la `S` fissa, sempre in prima posizione,
indipendentemente dal testo del tag.

**Correzione:** eliminato `String` da tutto il percorso seriale, sostituito con buffer
`char` a dimensione fissa:
- `listofDataToSend` è ora `char[MAX_NUMBER_OF_COMMANDS_TO_BOARD][OUTGOING_ITEM_LEN]`,
  costruito con la nuova `queueOutgoing(fmt, ...)` (wrapper su `vsnprintf`, stile printf,
  con controllo automatico del limite `MAX_NUMBER_OF_COMMANDS_TO_BOARD` — prima assente).
- `receivedCommands` è ora `char[MAX_NUMBER_OF_COMMANDS_TO_BOARD][INCOMING_CMD_LEN]`,
  scritto direttamente da `readFromBoard()` (niente più buffer di appoggio da ricopiare
  in una `String`), con limite sia sull'indice del comando sia sulla lunghezza del
  singolo token — prima assenti, quindi un comando anomalo poteva scrivere fuori
  dall'array.
- `splitCommand()` non prende più una `String`: lavora su `const char*` e riempie
  `tag`/`value`/`uid` (buffer `char` a dimensione fissa passati dal chiamante) usando
  `strchr`/`strncpy` + una nuova `trimInPlace()` equivalente a `String::trim()`.
- `pendingACK` è un `char[ACK_LEN]` composto con `snprintf`, non più concatenazione di
  `String`.

Nessuna allocazione dinamica resta nel percorso seriale: niente più heap da frammentare,
né su UNO né su Mega. Il rinominato di test `WWX01` è stato rimesso a `WGT01`.

**Verifica:** revisione manuale riga per riga (nessun toolchain AVR/Arduino disponibile
su questa macchina di sviluppo — vedi [[app-non-eseguibile-su-questa-macchina]]). Da
compilare e verificare sul Pi/UNO: il tag deve arrivare integro (`<WGT01,...>`), e la
scheda deve continuare a rispondere agli ACK (`HTR01`/`HUMER01`/`STPR01`/`ELV01`/`PWM01`/`FAN01`)
esattamente come prima.

---

## RISOLTO: flusso continuo di ACK `IND_CW`/`IND_CCW` dopo il fix precedente

**Sintomo:** dopo il fix di `WGT01`/`SGT01` sopra, in Python compaiono in continuazione
righe `Acknowledge received from external: IND_CW`/`IND_CCW`, senza pause ragionevoli.
Confermato: `STPR01` viene inviato dal PC **una volta sola** (log `Sent to Arduino:
<STPR01,...>` compare una sola volta), quindi non è Python a comandare il motore più
volte — è Arduino a rimandare l'evento di finecorsa raggiunto più e più volte da solo.

**Causa:** la notifica al PC (`<IND_CCW,1>`/`<IND_CW,1>`) partiva da
`ccw_trigger.catchRisingEdge()` / `cw_trigger.catchRisingEdge()` — oggetti della classe
`trigger` di `ProfiloLibrary` (libreria esterna, sorgente non disponibile in questo
repo). Una volta raggiunto un finecorsa, `getCCW()`/`getCW()` restano `true` finché non
riparte un movimento nella direzione opposta (ore, in produzione). Se quella classe si
comporta come un rilevamento di **livello** invece che di **fronte di salita**, ogni
singolo giro di `loop()` da quel momento in poi ri-accoda l'evento — esattamente il
sintomo osservato. `ccw_trigger`/`cw_trigger` non erano usati da nessun'altra parte
dello sketch, quindi non c'è alcun altro punto che li protegga da questo comportamento.

**Correzione:** tolta la dipendenza da quella libreria per questa notifica specifica.
Sostituita con un confronto esplicito, scritto a mano e autosufficiente, fra il valore
corrente di `getCCW()`/`getCW()` e quello del giro precedente (`_prevCCWLimit`/
`_prevCWLimit`, due `bool` globali): l'evento viene accodato solo quando il valore
passa da `false` a `true`. Rimossi `ccw_trigger`/`cw_trigger` (dead code, non più letti
da nessuno). Non toccato invece `motor_moveCCW_cmd_trigger`/`motor_moveCW_cmd_trigger`/
`motor_stop_cmd_trigger` (altri oggetti `trigger`, usati per rilevare l'arrivo dei
comandi `STPR01` in `case 100`): quel meccanismo, per stessa ammissione dell'utente,
funziona correttamente (un comando STPR01 → un solo movimento).

**Perché non si era mai visto prima:** più probabile spiegazione, non verificata:
la stessa frammentazione dell'heap da `String` (vedi sezione precedente) corrompeva
probabilmente anche `<IND_CCW,1>`/`<IND_CW,1>` in modo silenzioso (stesso meccanismo,
tag diverso), quindi buona parte di questi eventi ripetuti veniva scartata da Python
per tag non riconosciuto — mascherando il problema. Tolta la corruzione, tutti gli
eventi arrivano puliti e il flusso ripetuto diventa visibile.

**Verifica:** confermato sul Pi/UNO dopo l'upload — il flusso continuo di ACK è sparito,
un comando `STPR01` produce ora un solo `Acknowledge received from external: IND_CCW`/
`IND_CW`. La classe `trigger` di `ProfiloLibrary` è stata comunque controllata a sorgente
(righe 479-496 di `ProfiloLibrary.cpp`) ed è implementata correttamente (vero rilevatore
di fronte, non di livello): l'ipotesi iniziale era sbagliata, ma non essendoci altro punto
nello sketch che spiegasse la ripetizione, resta plausibile che la causa reale fosse una
copia diversa/precedente della libreria linkata durante la compilazione (risolta
copiando `ProfiloLibrary` dentro la cartella dello sketch, priorità sulla copia globale),
oppure un effetto laterale della `String` frammentata risolta nella sezione precedente. Il
rilevamento del fronte fatto a mano (`_prevCCWLimit`/`_prevCWLimit`) resta comunque in
sketch: autosufficiente, non dipende più da `ccw_trigger`/`cw_trigger`.

---

## Possibili prossimi passi (idee)

- [ ] Notifiche/allarmi in-browser quando la temperatura esce dal range (badge rosso in sidebar)
- [ ] Pagina di log in tempo reale (stream del file di log giornaliero)
- [ ] Autenticazione base (password) per l'accesso web remoto
- [ ] Grafici separati per umidità e peso acqua (già tutti i dati CSV disponibili)
- [ ] Export CSV dei dati direttamente dall'interfaccia web
- [ ] Grafico PID duty cycle sovrapposto alla temperatura (secondo asse Y)

---

## Come riprendere il lavoro

1. Aprire Claude Code nella cartella `AutomaticEggIncubator`
2. Il contesto del progetto è salvato nella memoria di Claude (si ricarica automaticamente)
3. Questo file `STATO_PROGETTO.md` riassume tutto

Per testare l'interfaccia sul Raspberry Pi:
```bash
cd /path/to/AutomaticEggIncubator
python3 eggsIncubatorMVVM.py
# Aprire http://<IP-Raspberry>:5000 nel browser
```
