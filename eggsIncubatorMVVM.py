import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import random
from PyQt5 import QtCore, QtWidgets
from eggsIncubatorGUI import Ui_MainWindow  # Import the UI class directly

import serial
import re
import os
from datetime import datetime, timedelta, date
import csv
import time
import queue
import subprocess
from collections import deque
import uuid
import json
import shutil
import math
import threading
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit as sio_emit

'''
    SALVATAGGIO DEI PARAMETRI:
    non prevedo un bottone in GUI, ogni volta che modifico un parametro sensibile questo viene automaticamente salvato nel file di parametri.
    All'avvio, in automatico, se un parametro è salvato allora ok viene usato, altrimenti rimane il default scritto nel codice
'''

'''
    i comandi devono essere quanto più univoci possibile! perhé ho visto che se metto CW e CCW alla domanda indexof() arduino non li sa distinguere
    self.queue_command("ACK", "1") - self.queue_command("ACK", "1")
    self.queue_command("HTR01", self.current_heater_output_control)  # @<HTR01, True># @<HTR01, False>#
    self.queue_command("HUMER01", self.current_humidifier_output_control) # @<HUMER01, True># @<HUMER01, False>#
    self.queue_command("STPR01", "MCCW") # move_counter_clock_wise
    self.queue_command("STPR01", "MCW") # move_clock_wise 
    self.queue_command("STPR01", "STOP") # stop motor   
'''

'''
Siccome ci sono dei thread e si rischia concorrenza fra la chiamata delle funzioni di log, è necessario farne due separate. Un logging per il SerialThread e un logging per
il MainSoftwareThread

1. INFO
Informational messages that highlight the progress of the application at a coarse-grained level. These are useful for tracking the general flow of the application.

2. DEBUG
Detailed information, typically of interest only when diagnosing problems. These messages are useful for developers to understand the internal state of the application.

3. WARNING
Indications that something unexpected happened, or indicative of some problem in the near future (e.g., ‘disk space low’). The software is still working as expected.

4. ERROR
Due to a more serious problem, the software has not been able to perform some function. These messages indicate a failure in the application.

5. CRITICAL
A serious error, indicating that the program itself may be unable to continue running. These messages are used for severe errors that require immediate attention.

6. ALARM
Specific to your application, this could be used to indicate conditions that require immediate attention but are not necessarily errors (e.g., temperature out of range).

7. EXCEPTION
Used to log exceptions that occur in the application. This can include stack traces and other debugging information.

Example of usage:

log_message('INFO', 'This is an informational message.')
log_message('WARNING', 'This is a warning message.')
log_message('ERROR', 'This is an error message.')
'''

# ARDUINO serial communication - setup #
# La stessa scheda ha nomi di porta diversi sui due sistemi: sul PC di sviluppo
# (Windows) è COM9, sul Raspberry che fa girare l'incubatrice è /dev/ttyUSB0.
# Scegliendola qui lo stesso file gira su entrambi senza modifiche a mano — che
# altrimenti si perdono a ogni copia del progetto da una macchina all'altra.
portSetup = "/dev/ttyUSB0" if sys.platform.startswith("linux") else "COM9"

baudrateSetup = 115200
timeout = 0.1

# Modalità leggera (?lite=1) del browser aperto in automatico all'avvio.
# None  → scelta storica: lite sul Pi (Linux), piena da PC in rete
# True  → forza sempre la modalità leggera
# False → forza sempre la modalità piena
BROWSER_LITE_MODE = None

"""
	"EXTT" = riguarda il sensore di temperatura esterno.
    "HTP"  = è la temperatura restituita dal sensore di umidità
    "WGT"  = WEIGHT è il valore da una certa cella di carico. La cella di carico 1 misura il peso della vaschetta di acqua
    
    "ELV01" = tag che riguarda il comando alla ELECTRO VALVE 01 = valvola per riempire il contenitore dell'acqua
    "PWM01" = è un valore INTERO da 0 a 255 che è il valore di PWM che Arduino deve impostare in uscita per far funzionare SSR con regolatore PID per la temperatura
    "FAN01" = valore FLOAT da 0.0 a 1.0 (percentuale/100) per il duty cycle PWM delle ventole di ricircolo aria
"""
identifiers = ["TMP", "HUM", "HTP", "IND", "EXTT", "WGT"]  # Global variable
command_tags = ["HTR01", "HUMER01", "STPR01", "ELV01", "PWM01", "FAN01"]

# ---------------------------------------------------------------------------
# Accesso robusto ai file
# La cartella del progetto è dentro OneDrive: mentre sincronizza un file lo
# tiene bloccato per qualche decina di ms e open() alza
# PermissionError [Errno 13] / OSError WinError 32. Lo stesso può fare
# l'antivirus. Ogni scrittura viene quindi ritentata e, se proprio fallisce,
# NON deve mai far cadere il thread di controllo (heater/PID/umidificatore).
# ---------------------------------------------------------------------------
FILE_ACCESS_RETRIES = 5
FILE_ACCESS_RETRY_DELAY = 0.02  # s, raddoppiato ad ogni tentativo (~0.6 s totali)


def open_with_retry(file_path, mode, **kwargs):
    """
    Come open(), ma ritenta se il file è temporaneamente bloccato da un altro
    processo (OneDrive, antivirus, editor aperto). Rilancia l'ultima eccezione
    se dopo tutti i tentativi il file è ancora inaccessibile.
    """
    delay = FILE_ACCESS_RETRY_DELAY
    last_error = None
    for attempt in range(FILE_ACCESS_RETRIES):
        try:
            return open(file_path, mode, **kwargs)
        except (PermissionError, OSError) as e:
            # errori "definitivi": inutile ritentare
            if isinstance(e, (FileNotFoundError, IsADirectoryError, NotADirectoryError)):
                raise
            last_error = e
            if attempt < FILE_ACCESS_RETRIES - 1:
                time.sleep(delay)
                delay *= 2
    raise last_error


def append_text_safe(file_path, text, encoding='utf-8'):
    """
    Appende testo a un file senza mai propagare eccezioni: il logging non deve
    poter uccidere il thread che lo chiama. Ritorna True se ha scritto.
    """
    try:
        with open_with_retry(file_path, 'a', encoding=encoding) as file:
            file.write(text)
        return True
    except Exception as e:
        # solo terminale: se il file di log non è scrivibile non ha senso loggare su file
        print(f"[LOG-FAIL] Impossibile scrivere su {file_path}: {e}")
        return False

# ---------------------------------------------------------------------------
# Web server (Flask + SocketIO) – replaces the PyQt5 MainWindow
# ---------------------------------------------------------------------------
flask_app = Flask(__name__)
flask_app.config['SECRET_KEY'] = 'incubator_secret_key_2024'
flask_app.config['TEMPLATES_AUTO_RELOAD'] = True
socketio = SocketIO(flask_app, cors_allowed_origins="*", async_mode='threading')
web_bridge = None  # assigned in __main__


@flask_app.route('/')
def index():
    return render_template('index.html')


@socketio.on('connect')
def on_connect():
    """Send current state to a newly connected browser."""
    if web_bridge:
        sio_emit('full_state', web_bridge.current_state)
        sio_emit('chart_history', list(web_bridge._chart_buf))


@socketio.on('button_click')
def on_button_click(data):
    if web_bridge:
        web_bridge.button_clicked.emit(data.get('name', ''))


@socketio.on('spinbox_change')
def on_spinbox_change(data):
    if web_bridge:
        web_bridge.float_spinBox_value_changed.emit(
            data.get('name', ''), float(data.get('value', 0.0))
        )


@socketio.on('radio_toggle')
def on_radio_toggle(data):
    if web_bridge:
        web_bridge.radio_button_toggled.emit(
            data.get('name', ''), bool(data.get('state', False))
        )


@socketio.on('date_change')
def on_date_change(data):
    if web_bridge:
        d = date.fromisoformat(data.get('date', '2000-01-01'))
        web_bridge.date_changed.emit('incubationStartDate', d)


@flask_app.route('/api/csv_data')
def api_csv_data():
    """Return CSV history data as JSON for the in-browser history charts.

    Query params:
      type  – folder name inside Machine_Statistics (e.g. 'Temperatures')
      mode  – 'today' | 'all' | 'mean'
    """
    import csv as _csv

    ALLOWED = {
        'Temperatures', 'External_Temperature', 'Humidity',
        'Heater', 'Humidifier', 'Water_Weight', 'PID_Duty_Cycle',
    }
    # Values outside these ranges are treated as sensor errors → replaced with None
    VALID_RANGES = {
        'Temperatures':         (-10.0, 80.0),
        'External_Temperature': (-20.0, 60.0),
        'Humidity':             (0.0, 100.0),
        'Water_Weight':         (0.0, 10000.0),
        'PID_Duty_Cycle':       (0.0, 1.0),
        'Heater':               (0.0, 1.0),
        'Humidifier':           (0.0, 1.0),
    }

    # Colonne che non sono misure ma riferimenti di controllo: vanno tracciate
    # con uno stile diverso e non entrano nel calcolo della media.
    REFERENCE_COLUMNS = {'SETPOINT', 'SP_MIN', 'SP_MAX'}

    data_type = request.args.get('type', 'Temperatures')
    mode      = request.args.get('mode', 'today')   # today | all | mean

    if data_type not in ALLOWED:
        return jsonify({'error': 'invalid type'}), 400

    script_dir = os.path.dirname(os.path.abspath(__file__))
    folder = os.path.join(script_dir, 'Machine_Statistics', data_type)

    if not os.path.isdir(folder):
        return jsonify({'series': [], 'title': f'No data for {data_type}'})

    today = datetime.now().strftime('%Y-%m-%d')

    if mode == 'today':
        csv_files = [os.path.join(folder, f'{today}.csv')]
    else:
        csv_files = sorted([
            os.path.join(folder, fn)
            for fn in os.listdir(folder) if fn.endswith('.csv')
        ])

    valid_min, valid_max = VALID_RANGES.get(data_type, (-1e9, 1e9))

    rows = []
    for path in csv_files:
        if not os.path.exists(path):
            continue
        try:
            with open_with_retry(path, 'r', newline='') as f:
                for row in _csv.DictReader(f):
                    try:
                        ts = datetime.strptime(row['Timestamp'], '%Y-%m-%d %H:%M:%S')
                        entry = {'ts': ts}
                        for k, v in row.items():
                            if k == 'Timestamp':
                                continue
                            s = (v or '').strip().lower()
                            if s == 'true':
                                fv = 1.0
                            elif s == 'false':
                                fv = 0.0
                            else:
                                try:
                                    fv = float(s)
                                except ValueError:
                                    # cella vuota o non numerica (es. colonna
                                    # aggiunta dopo): buco nel grafico, ma la
                                    # riga resta valida per le altre colonne
                                    entry[k] = None
                                    continue
                            # Replace sensor error values with None (shows as gap)
                            entry[k] = fv if valid_min <= fv <= valid_max else None
                        rows.append(entry)
                    except Exception:
                        pass
        except Exception:
            pass

    rows.sort(key=lambda r: r['ts'])

    # Collect column names in order of first appearance
    col_names = []
    for r in rows:
        for k in r:
            if k != 'ts' and k not in col_names:
                col_names.append(k)

    ts_strs = [r['ts'].strftime('%Y-%m-%dT%H:%M:%S') for r in rows]

    if mode == 'mean' and col_names:
        # I riferimenti di controllo non sono misure: vanno esclusi dalla media
        # dei sensori, altrimenti la "Mean" verrebbe tirata verso il setpoint.
        measured = [k for k in col_names if k not in REFERENCE_COLUMNS]
        mean_y = []
        for r in rows:
            vals = [r[k] for k in measured if r.get(k) is not None]
            mean_y.append(round(sum(vals) / len(vals), 3) if vals else None)
        series = [{'name': 'Mean', 'x': ts_strs, 'y': mean_y}]
        # ...ma restano utili come riferimento sovrapposto alla media
        series += [
            {'name': k, 'x': ts_strs, 'y': [r.get(k) for r in rows], 'reference': True}
            for k in col_names if k in REFERENCE_COLUMNS
        ]
    else:
        series = [
            {'name': k, 'x': ts_strs, 'y': [r.get(k) for r in rows],
             'reference': k in REFERENCE_COLUMNS}
            for k in col_names
        ]

    label_map = {'today': 'Today', 'all': 'All Days', 'mean': 'Mean – All Days'}
    title = f"{data_type.replace('_', ' ')} – {label_map.get(mode, mode)}"
    return jsonify({'series': series, 'title': title})

# ---------------------------------------------------------------------------


class SerialThread(QtCore.QThread):
    data_received = QtCore.pyqtSignal(list)
    board_reset_detected = QtCore.pyqtSignal()  # Arduino ripartito: vedi tag BOOT

    def __init__(self, port = portSetup, baudrate = baudrateSetup):
        super().__init__()
        self.port = port
        self.baudrate = baudrate
        self.command_queue = queue.Queue()  # Queue for outgoing commands
        self.retry_interval = 1 #sleeping time in seconds
        self.max_retries = 5
        self.running = True
        self.serial_port = None  # To store the serial port object
        self.serial_port_successfully_opened = False
        self.serial_thread_ready_to_go = False
        
        self.awaiting_ack = None
        self.ack_received_flag = False
        self.failed_commands = []

        # Diagnostica scheda (tag UPT/MEM nel frame periodico)
        self.board_uptime_ms = None
        self.board_free_ram = None
        self.last_boot_log_time = 0.0
        self.boot_burst_count = 0
        self.max_retries = 3
        self.ack_timeout = 0.5 # time.time() returns seconds. Suggested is 300-500ms
        
        script_dir = os.path.dirname(os.path.abspath(__file__))
        # Create a Log folder if it doesn't exist
        self.serial_thread_log_folder_path = os.path.join(script_dir, 'SerialThread_Log')
        if not os.path.exists(self.serial_thread_log_folder_path):
            os.makedirs(self.serial_thread_log_folder_path)
        '''
        self.pending_command = None
        self.failed_commands = []
        self.ack_timeout = 0.5  # secondi
        self.max_retries = 3
        self.last_sent_time = None
        '''
    def open_serial_port(self):
        retries = 0
        while retries < self.max_retries:
            try:
                self.serial_thread_log_message('INFO', 'Estabilishing serial connection')
                self.serial_port = serial.Serial(self.port, self.baudrate, timeout=1)
                time.sleep(3)
                self.serial_thread_log_message('INFO', 'Serial port opened successfully')
                # Discard any initial data to avoid decode errors
                self.serial_port.reset_input_buffer()
                self.serial_port.reset_output_buffer()
                return True
            except serial.SerialException as e:
                self.serial_thread_log_message('ERROR', f"Failed to open serial port: {e}. Retrying in {self.retry_interval} seconds...")
                time.sleep(self.retry_interval)
                retries += 1
        return False
 
    def run(self):
        self.serial_port_successfully_opened = self.open_serial_port()
        if not self.serial_port_successfully_opened:
            self.serial_thread_log_message('ERROR', 'Unable to open serial port after multiple attempts.')
            return
        sync_waiting_time_s = 5
        self.serial_thread_log_message('INFO', f"Waiting {sync_waiting_time_s} seconds for Arduino to setup")
        time.sleep(sync_waiting_time_s)
        self.serial_thread_ready_to_go = True

        buffer = ''
        startRx = False
        saving = False
        identifiers_data_list = []

        decode_error_count = 0
        decode_error_threshold = 10  # Number of decode errors before resetting serial port

        '''
        def log_error(msg):
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ERROR: {msg}")
        '''
        
        try:
            while self.running:
                while self.serial_port.in_waiting > 0:
                    try:
                        dataRead = self.serial_port.read().decode('utf-8')
                        decode_error_count = 0
                    except UnicodeDecodeError as e:
                        decode_error_count += 1
                        self.serial_thread_log_message('ALARM', f"Decode error (#{decode_error_count}): {e}. Resetting buffer and flags.")
                        
                        startRx = False
                        buffer = ''
                        saving = False

                        if decode_error_count >= decode_error_threshold:
                            self.serial_thread_log_message('CRITICAL', 'Too many decode errors. Resetting serial port.')
                            self.close_serial_port()
                            time.sleep(1)
                            self.open_serial_port()
                            decode_error_count = 0
                        continue

                    if dataRead == '@':
                        startRx = True
                        buffer = ''
                        identifiers_data_list.clear()
                        saving = False
                    elif startRx:
                        buffer += dataRead

                        if dataRead == '#':
                            # Messaggio completo ricevuto, es: @<TMP01,17.5><HTR01, True, 08CA9775><STPR01, False, 1234ABCD>#
                            startRx = False
                            trimmed_buffer = buffer.strip('@').strip('#')
                            #print(f"🔍 Full buffer received: {trimmed_buffer!r}")
                            if trimmed_buffer:
                                self.decode_message(self, trimmed_buffer, identifiers_data_list, self)

                            buffer = ''

                            if identifiers_data_list:
                                self.data_received.emit(identifiers_data_list)
                                identifiers_data_list.clear()

                self._try_send_next_command()
                
                time.sleep(0.1)


        except serial.SerialException as e:
            self.serial_thread_log_message('ERROR', f"Serial error: {e}")
            
        finally:
            self.close_serial_port()
            
    def _try_send_next_command(self):
        # Se sto già aspettando un ACK, controllo timeout/flag
        if self.awaiting_ack:
            if self.ack_received_flag:
                # ACK arrivato: sblocco
                self.awaiting_ack = None
                self.ack_received_flag = False
                return
            elif time.time() - self.awaiting_ack["timestamp"] >= self.ack_timeout:
                cmd_obj = self.awaiting_ack["command"]
                retries = cmd_obj["retry_count"] + 1
                if retries <= self.max_retries:
                    cmd_obj["retry_count"] = retries
                    self.serial_port.write(cmd_obj["formatted"].encode('utf-8'))
                    # Stampiamo solo se non è ALIVE
                    if cmd_obj["cmd"] != "ALIVE":
                        self.serial_thread_log_message('WARNING', f"Retrying ({retries}) for: {cmd_obj['formatted']}")
                    self.awaiting_ack["timestamp"] = time.time()
                else:
                    # Stampiamo solo se non è ALIVE
                    if cmd_obj["cmd"] != "ALIVE":
                        self.serial_thread_log_message('ALARM', f"⚠️ NO ACK for command: {cmd_obj['formatted']}")
                    self.failed_commands.append(cmd_obj)
                    self.awaiting_ack = None
            return

        # Altrimenti, se non ho comandi in attesa, ne prendo uno nuovo
        if not self.command_queue.empty():
            cmd_obj = self.command_queue.get()
            formatted = cmd_obj["formatted"]
            uid = cmd_obj["id"]

            if uid is None:
                # Comando senza ACK
                self.serial_port.write(formatted.encode('utf-8'))
                # Stampiamo solo se non è ALIVE
                if cmd_obj["cmd"] != "ALIVE":
                    self.serial_thread_log_message('INFO', f"Sent (no ACK needed): {formatted}")
            else:
                # Comando con ACK
                self.serial_port.write(formatted.encode('utf-8'))
                # Stampiamo solo se non è ALIVE
                if cmd_obj["cmd"] != "ALIVE":
                    self.serial_thread_log_message('INFO', f"Sent to Arduino: {formatted}")
                cmd_obj["retry_count"] = 0
                self.awaiting_ack = {
                    "uid": uid,
                    "command": cmd_obj,
                    "timestamp": time.time()
                }
            
    def add_command(self, cmd, value):
        if cmd in command_tags:
            unique_id = uuid.uuid4().hex[:8].upper()  # ID breve, es. '3F7A91B2'
            formatted_command = f"@<{cmd}, {value}, {unique_id}>#"
        else:
            unique_id = None
            formatted_command = f"@<{cmd}, {value}>#"

        self.command_queue.put({
            "cmd": cmd,
            "value": value,
            "id": unique_id,
            "formatted": formatted_command,
            "retry_count": 0,
        })
    
    def stop(self):
        self.running = False
        self.wait()  # Ensure the thread finishes before returning
        self.close_serial_port()
        
    def close_serial_port(self):
        if self.serial_port and self.serial_port.is_open:
            try:
                self.serial_port.close()
                self.serial_thread_log_message('INFO', 'Serial port closed successfully')
            except Exception as e:
                self.serial_thread_log_message('ERROR', f"Error closing serial port: {e}")

    @staticmethod
    def decode_message(self, buffer, identifiers_data_list, serial_thread_instance):
        import re

        # Prende tutte le sottostringhe tra < e >
        messages = re.findall(r"<([^>]+)>", buffer)
        #print(f"🔍 Messages extracted: {messages}")
        for msg in messages:
            parts = [p.strip() for p in msg.split(',')]
            #print(f"🔍 Parsing parts: {parts}")
            #print(f"⏳ awaiting_ack right now: {serial_thread_instance.awaiting_ack!r}")

            if len(parts) == 3:
                cmd, value, uid = parts
                #print(f"🔍 Detected potential ACK - cmd={cmd}, value={value}, uid={uid}")
                # Se è un comando che richiede ACK e corrisponde all'atteso
                if cmd in command_tags:
                    if (serial_thread_instance.awaiting_ack and
                        serial_thread_instance.awaiting_ack["uid"] == uid):
                        self.serial_thread_log_message('INFO', f"✅ ACK ricevuto: {cmd}, {value}, ID={uid}")
                        serial_thread_instance.ack_received_flag = True
                else:
                    self.serial_thread_log_message('ALARM', f"⚠️ ACK {uid} non atteso o awaiting_ack differente")
                    # Non aggiungo ACK alla lista dati normali
                    continue

            elif len(parts) == 2:
                info_name, info_value = parts

                if info_name == "BOOT":
                    # Arduino è ripartito: setup() ha rimesso i relè a LOW e u_cmd a 0.0,
                    # quindi lo stato che il PC crede di aver impostato non è più vero.
                    #
                    # Il riavvio può ripetersi decine di volte al secondo (crash loop):
                    # log e segnale vanno limitati a uno al secondo, altrimenti si
                    # allaga il file di log e si martella la scheda di comandi.
                    now_boot = time.time()
                    serial_thread_instance.boot_burst_count += 1
                    if (now_boot - serial_thread_instance.last_boot_log_time) >= 1.0:
                        burst = serial_thread_instance.boot_burst_count
                        serial_thread_instance.boot_burst_count = 0
                        serial_thread_instance.last_boot_log_time = now_boot
                        self.serial_thread_log_message(
                            'ALARM', f"⚠️ Arduino riavviato (BOOT) x{burst} nell'ultimo secondo")
                        serial_thread_instance.board_reset_detected.emit()
                    continue

                if info_name == "MEM":
                    serial_thread_instance.board_free_ram = int(float(info_value))
                    continue

                if info_name == "UPT":
                    # DIAGNOSTICA dei "silenzi" di ~2.4 s sulla seriale.
                    # L'uptime della scheda distingue le tre cause possibili, che
                    # dal PC sono altrimenti indistinguibili.
                    uptime_ms = int(float(info_value))
                    previous = serial_thread_instance.board_uptime_ms
                    serial_thread_instance.board_uptime_ms = uptime_ms
                    ram = serial_thread_instance.board_free_ram

                    if previous is not None:
                        if uptime_ms < previous:
                            self.serial_thread_log_message(
                                'ALARM',
                                f"⚠️ RESET SCHEDA: uptime tornato indietro "
                                f"({previous} -> {uptime_ms} ms), RAM libera {ram} B")
                            serial_thread_instance.board_reset_detected.emit()
                        elif (uptime_ms - previous) > 1500:
                            self.serial_thread_log_message(
                                'ALARM',
                                f"⚠️ STALLO FIRMWARE: {uptime_ms - previous} ms fra due frame "
                                f"(nessun reset, uptime continuo), RAM libera {ram} B")
                    continue

                if info_value.upper() == "NAN":
                    number = 0.0
                elif SerialThread.is_number(info_value):
                    number = float(info_value) if '.' in info_value else int(info_value)
                else:
                    self.serial_thread_log_message('WARNING', f"Non-numeric data: {info_value}")
                    continue

                for identifier in identifiers:
                    if info_name.startswith(identifier):
                        identifiers_data_list.append({info_name: number})
                        break

            
    def handle_ack(self, tag, value, uid):
        if self.awaiting_ack and self.awaiting_ack["uid"] == uid:
            self.serial_thread_log_message('INFO', f"✅ ACK ricevuto: {tag}, {value}, ID={uid}")
            self.ack_received_flag = True
        else:
            self.serial_thread_log_message('ALARM', f"⚠️ ACK ricevuto ma non atteso o ID diverso: {uid}")
    
    @staticmethod
    def is_number(s):
        try:
            float(s)
            return True
        except ValueError:
            return False
        
    def serial_thread_log_message(self, error_type, message):
        """
        Logs a message to a log file named with the current date inside the Log folder.
        Format: [ERROR_TYPE] Message @ Timestamp

        Args:
            error_type (str): The type of error (e.g., 'INFO', 'WARNING', 'ERROR').
            message (str): The message to log.
        """
        print(message)
        current_date = datetime.now().strftime('%Y-%m-%d')
        log_file = os.path.join(self.serial_thread_log_folder_path, f"log_{current_date}.txt")

        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        append_text_safe(log_file, f"[{error_type}] {message} @ {timestamp}\n")
        '''
        OLD VERSION FOR JSON FORMAT
        """
        Logs a message to a log file named with the current date inside the Log folder in plain text format with sections.

        Args:
            error_type (str): The type of error (e.g., 'INFO', 'WARNING', 'ERROR').
            message (str): The message to log.
        """
        print(message)
        current_date = datetime.now().strftime('%Y-%m-%d')
        log_file = os.path.join(self.serial_thread_log_folder_path, f"log_{current_date}.txt")
        
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        with open(log_file, 'a') as file:
            file.write(f"--- Log Entry ---\n")
            file.write(f"Timestamp: {timestamp}\n")
            file.write(f"Error Type: {error_type}\n")
            file.write(f"Message: {message}\n")
            file.write(f"-----------------\n\n")
        '''


class MainSoftwareThread(QtCore.QThread):
    update_view = QtCore.pyqtSignal(list)  # Signal to update the view (MainWindow)
    update_statistics = QtCore.pyqtSignal(list) # Signal to update the Statistics View Window
    update_days_statistics = QtCore.pyqtSignal(list) # Signal to update the Statistics View Window
    update_spinbox_value = QtCore.pyqtSignal(str, float)  # Signal to update spinbox (name, value)
    update_int_spinbox_value = QtCore.pyqtSignal(str, int)  # Signal to update spinbox (name, value)
    update_motor = QtCore.pyqtSignal(list) # Signal to update the motor view
    update_date_edit = QtCore.pyqtSignal(str, object)
    update_radio_button_exclusive = QtCore.pyqtSignal(str, bool) #Se i radio button fanno parte dello stesso gruppo (stesso layout o QButtonGroup), PyQt gestisce automaticamente l’esclusività: selezionare uno li deselezionerà tutti gli altri del gruppo.
    
    def __init__(self):
        super().__init__()
        self.initialization_from_GUI_completed = False
        self.running = True
        self.serial_thread = SerialThread()
        self.serial_thread.data_received.connect(self.process_serial_data)
        self.serial_thread.board_reset_detected.connect(self.handle_board_reset)
        self.current_data = []  # Holds the most recent data received from SerialThread
        
        self._last_today = date.today()
        
        self.alive_to_arduino_state = False
        self.alive_to_arduino_time_interval_sec = 2 # invio un ack ad arduino 2 secondi
        self.last_alive_to_arduino_time = time.time()
        
        self.saving_interval = 1 # default: every minute
        self.last_saving_time = datetime.now()

        self.saving_interval_generalPurposeSaving = 1 # default: every second
        self.last_saving_time_generalPurposeSaving = datetime.now()
        self.arduino_readings_timestamp  = time.time() # per ricordarmi l'istante di tempo in cui arduino manda i dati
        
        self.command_list = [] # List to hold the pending commands
        
        # Incubation Days statistics
        self.incubation_start_date = None 
        self.incubation_start_date_previous = None 
        # print(self.incubation_start_date)      # 2025-12-27
        # type(self.incubation_start_date)       # <class 'datetime.date'>
        self.incubation_duration_days = 0
        self.incubation_duration_days_previous = 0
        self.days_passed = 0
        self.days_passed_previous = 0
        self.days_left = 0
        self.days_left_previous = 0
        self.incubation_end_date = None
        self.incubation_end_date_previous = None

        # === PID Controller with Automatic Timing === #
        # SYSTEM PARAMETERS

        '''
        CHAT GPT
        self.pid_temperature = self.PIDController()
        self.pid_temperature.set_output_limits(0, 100)  # PID outputs 0–100%
        self.pid_temperature.set_reference_value(37.8) 
        '''

        # COPILOT       
        self.multiplier = 3
        self.Kp = 11.28
        self.Ki = 0.000995    # [1/s]
        self.Kd = 0.0         # non usiamo il derivativo
        self.Ts = 1.0         # [s] ciclo PID
        self.K_process = 271.62  # °C per unità (u in [0..1]) dal fit - lavorare su questo per il ff
        self.Tamb = 12.7         # °C (aggiornalo se lo misuri)

        self.pid_temperature = self.PIDController(kp=self.Kp, ki=self.Ki, kd=self.Kd,
                            reference=37.8,
                            output_min=0.0, output_max=1.0)

        # Abilita il feedforward termico
        self.pid_temperature.set_feedforward_params(K_process=self.K_process, ambient_value=self.Tamb, enable=True)



        self.pid_temperature_is_activated = False # by default hysteresis controller is active!
        self.pwm = 0 #value to store output for arduino

        # Ventole ricircolo aria: percentuale 0-100 impostata dall'utente (non calcolata,
        # a differenza del duty PID), inviata ad Arduino come FAN01 (0.0-1.0).
        self.fan_speed_percent = 0.0

        # Ultimo duty inviato ad Arduino + istante di invio. Il comando PWM01 viene
        # ripetuto periodicamente anche a valore invariato: vedi process_serial_data.
        self.last_pwm_sent = 0.0
        self._last_pwm_sent_time = 0.0   # 0.0 -> il primo invio parte subito
        self.PWM_RESEND_INTERVAL_SEC = 5.0

        # Riallineamento dopo un riavvio della scheda: vedi handle_board_reset
        self._last_board_resync_time = 0.0
        self._board_reset_window_start = 0.0
        self._board_reset_count = 0
        self.BOARD_RESYNC_MIN_INTERVAL_SEC = 5.0
        self.BOARD_RESET_WINDOW_SEC = 30.0
        self.BOARD_RESET_LOOP_THRESHOLD = 3

        
        # State variables to handle inputs from MainWindow
        self.current_button = None # notifica del pulsante premuto
        self.spinbox_values = {} # notifica dello spinbox cambiato
        
        # TEMPERATURE CONTROLLER
        # Constants
        self.configured_heater_power = 0.158; # WATT
        self.INVALID_VALUES = [-127.0, 85.0]  # Known error values
        self.THRESHOLD = 0.5  # Acceptable variation from the valid range
        self.VALID_RANGE_TEMPERATURE = (5.0, 80.0)  # Expected temperature range
        self.VALID_RANGE_HUMIDITY = (0.0, 100.0) # Expected humidity range
        self.VALID_RANGE_WATER_LEVEL = (0.0, 5.0) # Expected water level range

        # ANTI-DEBOUNCE for thc temperature hysteresis controller
        self._last_output_change_time_thc = None
        self._last_output_state_thc = None
        self._debounced_heater_output_thc = None  # Stato effettivamente inviato
        
        # ANTI-DEBOUNCE for thc humidity hysteresis controller
        self._last_output_change_time_hhc = None
        self._last_output_state_hhc = None
        self._debounced_heater_output_hhc = None  # Stato effettivamente inviato
        
        # ANTI-DEBOUNCE for thc water level control hysteresis controller
        self._last_output_change_time_whc = None
        self._last_output_state_whc = None
        self._debounced_heater_output_whc = None  # Stato effettivamente inviato
        
        self._debounce_duration = 1.0  # secondi
        
        
        
        # Error tracking
        self.ERROR_TIME_LIMIT = 10  # Tempo in secondi oltre il quale generare un warning
        # Stato del sistema
        self.error_counter = 0  # Conta tutti gli errori mai verificati
        self.error_timestamps = {}  # Memorizza il primo timestamp di errore per ogni sensore
        self.warned_sensors = set()  # Tiene traccia dei sensori già segnalati con un warning

        self.thc = self.HysteresisController(lower_limit = 37.5, upper_limit = 37.8) # temperature hysteresis controller
        self.current_heater_output_control = False # variabile che mi ricorda lo stato attuale dell'heater
        
        self.hhc = self.HysteresisController(lower_limit = 20.0, upper_limit = 50.0) # humidity hysteresis controller
        self.current_humidifier_output_control = False # variabile che mi ricorda lo stato attuale dell'elettrovalvola pneumatica
        
        self.whc = self.HysteresisController(lower_limit = 1.0, upper_limit = 4.0) # water hysteresis controller
        self.current_waterLevel_output_control = False # variabile che mi ricorda lo stato attuale dell'elettrovalvola per l'acqua
        
        self.eggTurnerMotor = self.StepperMotor("Egg_Turner_Stepper_Motor")
        self.last_turnsCounter = 0 # serve per ricordarmi il numero di turns counter
        
		
		# PLOT
        self.remove_erroneous_values_from_T_plot = True
        self.remove_erroneous_values_from_H_plot = True
        
        #--- Create Machine_Statistic folder ---#
        script_dir = os.path.dirname(os.path.abspath(__file__))
        machine_statistics_folder_path = os.path.join(script_dir, "Machine_Statistics") 
        if not os.path.exists(machine_statistics_folder_path):
                os.makedirs(machine_statistics_folder_path)

        # --- Create Statistics folder ---#
        external_temperature_folder_path = os.path.join(machine_statistics_folder_path, 'External_Temperature')
        if not os.path.exists(external_temperature_folder_path):
            os.makedirs(external_temperature_folder_path)
            
        temperatures_folder_path = os.path.join(machine_statistics_folder_path, 'Temperatures')
        if not os.path.exists(temperatures_folder_path):
            os.makedirs(temperatures_folder_path)

        humidity_folder_path = os.path.join(machine_statistics_folder_path, 'Humidity')
        if not os.path.exists(humidity_folder_path):
            os.makedirs(humidity_folder_path)
            
        heater_actuator_folder_path = os.path.join(machine_statistics_folder_path, 'Heater')
        if not os.path.exists(heater_actuator_folder_path):
            os.makedirs(heater_actuator_folder_path)

        humidifier_actuator_folder_path = os.path.join(machine_statistics_folder_path, 'Humidifier')
        if not os.path.exists(humidifier_actuator_folder_path):
            os.makedirs(humidifier_actuator_folder_path)
            
        water_weight_actuator_folder_path = os.path.join(machine_statistics_folder_path, 'Water_Weight')
        if not os.path.exists(water_weight_actuator_folder_path):
            os.makedirs(water_weight_actuator_folder_path)
            
        PID_Duty_Cycle_folder_path = os.path.join(machine_statistics_folder_path, 'PID_Duty_Cycle')
        if not os.path.exists(PID_Duty_Cycle_folder_path):
            os.makedirs(PID_Duty_Cycle_folder_path)

        general_purpose_folder_path = os.path.join(machine_statistics_folder_path, 'General_Purpose')
        if not os.path.exists(general_purpose_folder_path):
            os.makedirs(general_purpose_folder_path)
            
        # Create a Log folder if it doesn't exist
        self.main_software_thread_log_folder_path = os.path.join(script_dir, 'MainSoftwareThread_Log')
        if not os.path.exists(self.main_software_thread_log_folder_path):
            os.makedirs(self.main_software_thread_log_folder_path)

        #--- Create Parameters folder + file ---#
        script_dir = os.path.dirname(os.path.abspath(__file__))
        parameters_folder_path = os.path.join(script_dir, "Parameters") 
        if not os.path.exists(parameters_folder_path):
                os.makedirs(parameters_folder_path)

        self.parameters_file_path = os.path.join(parameters_folder_path, "parameters.json") # file where parameters are saved
        self.parameters = {} # dictionary storing all parameters in program memory
        self._load_all_parameters() # loading already existing parameters in the file

    def run(self):   
        self.write_log_section_header()
        self.main_software_thread_log_message('INFO', f"Starting serial Thread")
        # Start the SerialThread
        self.serial_thread.start()
        
        while not self.serial_thread.serial_thread_ready_to_go:
            pass
        self.main_software_thread_log_message('INFO', f"Serial Thread started, waiting for GUI initialization")

        # prima di far partire il loop provo già a settare i paramteri corretti
        while not self.initialization_from_GUI_completed:
            pass
         #1x volta, inizializzatione dei paramteri da file
         # Initializing parameters from file:
        self.parameters_initialization_from_file()
        self.backup_parameters_file()
        self.main_software_thread_log_message('INFO', f"GUI initialization completed: loading parameters from file is done. Now the program starts!")

        while self.running:            
            if self.command_list:
                for cmd, value in self.command_list:
                    self.serial_thread.add_command(cmd, value)
                    if cmd != "ALIVE":
                        self.main_software_thread_log_message('INFO', f"Added commad to serial thread queue: {cmd}, {value}")
                    else:
                        self.main_software_thread_log_message('INFO', f"Added commad to serial thread queue: {cmd}, {value},", True) # suppress terminal print
                self.command_list.clear()
                
            # parte che gira periodica, quindi ci metto la gestione del motore siccome deve funzionare periodicamente per il timer.
            self.eggTurnerMotor.update()
            
            if self.eggTurnerMotor.getNewCommand() is not None: # checking if there's a command
                self.main_software_thread_log_message('INFO', f"Processing command: {self.eggTurnerMotor.getNewCommand()}")
                
                if (self.eggTurnerMotor.getNewCommand() == "automatic_CCW_rotation_direction"
                    or
                    self.eggTurnerMotor.getNewCommand() == "manual_CCW_rotation_direction"):
                    self.queue_command("STPR01", "MCCW") # move_counter_clock_wise
                    
                if (self.eggTurnerMotor.getNewCommand() == "automatic_CW_rotation_direction"
                    or
                    self.eggTurnerMotor.getNewCommand() == "manual_CW_rotation_direction"):
                    self.queue_command("STPR01", "MCW") # move_clock_wise           
                    
                if self.eggTurnerMotor.getNewCommand() == "stop":
                    self.queue_command("STPR01", "STOP") # stop motor 
                
                self.eggTurnerMotor.resetNewCommand()
            
            if self.eggTurnerMotor.getUpdateMotorData():
                all_values = []
                # [timePassed timeToNextTurn turnsCounter]
                all_values = [self.eggTurnerMotor.getTimeSinceLastRotation()] + \
                                [self.eggTurnerMotor.getTimeUntilNextRotation()] + \
                                [self.eggTurnerMotor.getTurnsCounter()] + \
                                [self.eggTurnerMotor.main_state] + \
                                [self.eggTurnerMotor.manual_state] + \
                                [self.eggTurnerMotor.rotation_state]
                self.update_motor.emit(all_values)

                if self.eggTurnerMotor.getTurnsCounter() != self.last_turnsCounter:
                    self.last_turnsCounter = self.eggTurnerMotor.getTurnsCounter() # salvo il numero nuovo di turns counter
                    self.save_parameter('TURNS_COUNTER', self.eggTurnerMotor.getTurnsCounter())
                
                self.eggTurnerMotor.resetUpdateMotorData()
            
            # GESTIONE DELL'ALIVE BIT verso arduino
            if (time.time() - self.last_alive_to_arduino_time) >= self.alive_to_arduino_time_interval_sec:
                self.last_alive_to_arduino_time = time.time()
                self.alive_to_arduino_state = not self.alive_to_arduino_state
                self.queue_command("ALIVE", self.alive_to_arduino_state)
                
            # statistiche giorni di incubata - controllo dei giorni passati
            # if time or cambia start date or cambia duration
            today = date.today()
            if ( 
                self.incubation_start_date_previous != self.incubation_start_date
                or self.incubation_duration_days_previous != self.incubation_duration_days
                or today != self._last_today
                ):
                # se cambio manualmente la data di inzio o la durata di incubata si aggiorna la visualizzazione. Oppure appena passa la mezzanotte, ovvero cambia il giorno.
                self.incubation_start_date_previous = self.incubation_start_date
                self.incubation_duration_days_previous = self.incubation_duration_days
                self._last_today = today                    
                self.update_incubation_state()
              
            time.sleep(0.1)
            
    def update_incubation_state(self, today=None):  # type: (date) -> None
        """
        Aggiorna:
        - days_passed
        - days_left
        - incubation_end_date
        """

        # sicurezza: dati non pronti
        if self.incubation_start_date is None or self.incubation_duration_days <= 0:
            self.days_passed = 0
            self.days_left = 0
            self.incubation_end_date = None
            return

        if today is None:
            today = date.today()

        # data finale
        self.incubation_end_date = (
            self.incubation_start_date + timedelta(days=self.incubation_duration_days)
        )

        # giorni passati
        self.days_passed = max(
            0,
            (today - self.incubation_start_date).days
        )

        # giorni rimanenti
        self.days_left = max(
            0,
            (self.incubation_end_date - today).days
        )
        
        if self.incubation_end_date_previous != self.incubation_end_date or self.incubation_end_date_previous is None:
            self.update_date_edit.emit("endDate_dateTimeBox", self.incubation_end_date)
        
        if (self.days_passed_previous != self.days_passed) or (self.days_left_previous != self.days_left):
            all_values = []
            all_values.append(self.days_passed)
            all_values.append(self.days_left)            
            self.update_days_statistics.emit(all_values)
            
        self.incubation_end_date_previous = self.incubation_end_date
        self.days_passed_previous = self.days_passed
        self.days_left_previous = self.days_left
        
            
        
            
    def check_errors(self,sensor_data):
        """Verifica errori, aggiorna il contatore e traccia il tempo degli errori persistenti."""

        current_time = time.time()
        new_errors = []  # Sensori che entrano in errore ora
        resolved_errors = []  # Sensori che sono tornati normali

        # Analizza il dizionario ricevuto
        #print(sensor_data)
        for sensor, value in sensor_data.items():
            # Verifica se il valore è un errore
            if any(abs(value - iv) < self.THRESHOLD for iv in self.INVALID_VALUES) or not (self.VALID_RANGE_TEMPERATURE[0] <= value <= self.VALID_RANGE_TEMPERATURE[1]):
                # Se è un nuovo errore, aggiorna il contatore e salva il timestamp
                if sensor not in self.error_timestamps:
                    self.error_timestamps[sensor] = current_time
                    self.error_counter += 1
                    new_errors.append(sensor)  # Sensore appena entrato in errore
            else:
                # Se il sensore era in errore ma ora non lo è più, lo rimuoviamo dal tracking
                if sensor in self.error_timestamps:
                    del self.error_timestamps[sensor]
                    self.warned_sensors.discard(sensor)  # Resetta anche il warning
                    resolved_errors.append(sensor)

        # Stampa solo se ci sono nuovi errori o warning per errori persistenti
        if new_errors:
            self.main_software_thread_log_message('ALARM', f"⚠ ERRORE: Sensori appena entrati in errore: {new_errors}")
            self.main_software_thread_log_message('INFO', f"Totale errori rilevati finora: {self.error_counter}")

        for sensor, start_time in self.error_timestamps.items():
            if current_time - start_time > self.ERROR_TIME_LIMIT and sensor not in self.warned_sensors:
                self.main_software_thread_log_message('WARNING', f"⚠ WARNING: Il sensore '{sensor}' è in errore da più di {self.ERROR_TIME_LIMIT} secondi!")
                self.warned_sensors.add(sensor)  # Segnala il warning solo una volta
            
    def filter_temperatures(self, temperatures):
        """
        La funzione prende in ingresso temperatures (lista di valori) e restituisce una nuova lista contenente solo i 
        valori di temperatura che passano due controlli: 
        - non sono vicini a valori di errore conosciuti 
        - e rientrano nell'intervallo 
        di temperatura valido. Ecco il comportamento passo-passo.
        Remove outliers and keep only valid temperature values.
        """

        return [
            temp for temp in temperatures
            if not any(abs(temp - iv) < self.THRESHOLD for iv in self.INVALID_VALUES)
            and self.VALID_RANGE_TEMPERATURE[0] <= temp <= self.VALID_RANGE_TEMPERATURE[1]
        ]

    def saturate_values(self, values, valid_range):
        """
        Applica una saturazione (clamping) ai valori della lista.
        - Se un valore è dentro valid_range → viene mantenuto.
        - Se è fuori → viene sostituito col valore limite più vicino.
        
        Args:
            values: lista di valori numerici
            valid_range: tupla (min_val, max_val)

        Returns:
            Lista con i valori saturati.
        """
        min_val, max_val = valid_range

        return [
            min(max(val, min_val), max_val)
            for val in values
        ]
            
    def queue_command(self, cmd, value):
        self.command_list.append((cmd, value))

    def handle_board_reset(self):
        """
        Chiamata quando Arduino segnala un riavvio (tag BOOT).

        Dopo un reset la scheda è tornata a riposo: setup() rimette tutti i relè a
        LOW e la globale u_cmd a 0.0. Senza questo riallineamento il PC resterebbe
        convinto di avere gli attuatori nello stato precedente e - non inviando
        comandi a valore invariato - non li ripristinerebbe mai.

        ATTENZIONE all'anello di retroazione: se è proprio il comando che stiamo
        ripristinando a far riavviare la scheda, il ripristino diventa un ciclo
        infinito (riavvio -> rinvio -> riavvio) che martella l'Arduino decine di
        volte al secondo. Perciò il riallineamento è limitato in frequenza e, se i
        riavvii continuano, il relè del riscaldatore smette di essere ripristinato.
        """
        now_reset = time.time()

        # Finestra scorrevole per riconoscere un crash loop
        if (now_reset - self._board_reset_window_start) > self.BOARD_RESET_WINDOW_SEC:
            self._board_reset_window_start = now_reset
            self._board_reset_count = 0
        self._board_reset_count += 1

        if (now_reset - self._last_board_resync_time) < self.BOARD_RESYNC_MIN_INTERVAL_SEC:
            return
        self._last_board_resync_time = now_reset

        heater_relay_is_suspect = self._board_reset_count >= self.BOARD_RESET_LOOP_THRESHOLD

        self.main_software_thread_log_message(
            'ALARM',
            f"⚠️ Arduino riavviato ({self._board_reset_count} riavvii negli ultimi "
            f"{self.BOARD_RESET_WINDOW_SEC:.0f} s): rinvio dello stato degli attuatori")

        if self.pid_temperature_is_activated:
            # In modo PID scalda l'SSR: il relè 1 resta spento di proposito.
            self.queue_command("PWM01", self.pwm)
            self.last_pwm_sent = self.pwm
            self._last_pwm_sent_time = now_reset
        elif self._debounced_heater_output_thc is not None:
            if heater_relay_is_suspect and self._debounced_heater_output_thc:
                self.main_software_thread_log_message(
                    'CRITICAL',
                    "⛔ Riavvii ripetuti: il comando HTR01 True sembra essere la causa. "
                    "Ripristino del relè riscaldatore SOSPESO per non alimentare il ciclo "
                    "di riavvii. Riscaldatore considerato SPENTO.")
                self._debounced_heater_output_thc = False
                self._last_output_state_thc = False
            else:
                self.queue_command("HTR01", self._debounced_heater_output_thc)

        if self._debounced_heater_output_hhc is not None:
            self.queue_command("HUMER01", self._debounced_heater_output_hhc)
        if self._debounced_heater_output_whc is not None:
            self.queue_command("ELV01", self._debounced_heater_output_whc)

        # Le ventole non dipendono dal riscaldatore: setup() le rimette a 0 su
        # ogni riavvio, quindi vanno riallineate a prescindere dal ramo sopra.
        self.queue_command("FAN01", round(self.fan_speed_percent / 100.0, 3))

    def stop(self):
        self.running = False
        self.serial_thread.stop()
        self.serial_thread.wait()
        
    def handle_button_click(self, button_name):
        self.main_software_thread_log_message('INFO', f"[MainSoftwareThread] Processing button {button_name}")
        self.current_button = button_name
        
        if self.current_button == "forceEggsTurn_motor_btn":
            self.eggTurnerMotor.forceEggsRotation()
        
        if self.current_button == "move_CW_motor_btn":
            self.eggTurnerMotor.pressButton("move_CW_motor_btn") # ogni volta in cui lo premo, il metodo farà opportunamente il toggle dello stato
            
        if self.current_button == "move_CCW_motor_btn":
            self.eggTurnerMotor.pressButton("move_CCW_motor_btn") # ogni volta in cui lo premo, il metodo farà opportunamente il toggle dello stato
				
        if self.current_button == "reset_statistics_T_btn":
            self.thc.reset_all_values()
        
        if self.current_button == "plotMeanTemperature_btn":
            script_dir = os.path.dirname(os.path.abspath(__file__))
            python_executable = self.get_python_executable()
            plot_script = os.path.join(script_dir, "appInteractivePlots.py")          
            command = [
                python_executable,
                plot_script,
                "PLOT_ALL_DAYS_DATA_MEAN_TEMPERATURE",
                str(self.VALID_RANGE_TEMPERATURE[0]),
                str(self.VALID_RANGE_TEMPERATURE[1]),
                str(True),
            ]
            self.main_software_thread_log_message('INFO', f"Command to plot data: {command}")
            process = subprocess.Popen(command)
            #print("Subprocess started and main program continues...")
            
        if self.current_button == "plotExternalTemperature_btn":
            script_dir = os.path.dirname(os.path.abspath(__file__))
            python_executable = self.get_python_executable()
            plot_script = os.path.join(script_dir, "appInteractivePlots.py")          
            command = [
                python_executable,
                plot_script,
                "PLOT_ALL_DAYS_DATA_EXTERNAL_TEMPERATURE",
                str(self.VALID_RANGE_TEMPERATURE[0]),
                str(self.VALID_RANGE_TEMPERATURE[1]),
                str(True),
            ]
            self.main_software_thread_log_message('INFO', f"Command to plot data: {command}")
            process = subprocess.Popen(command)
            #print("Subprocess started and main program continues...")
            
        if self.current_button == "plotAllDays_temp_T_btn":
            script_dir = os.path.dirname(os.path.abspath(__file__))
            python_executable = self.get_python_executable()
            plot_script = os.path.join(script_dir, "appInteractivePlots.py")          
            command = [
                python_executable,
                plot_script,
                "PLOT_ALL_DAYS_DATA_TEMPERATURES",
                str(self.VALID_RANGE_TEMPERATURE[0]),
                str(self.VALID_RANGE_TEMPERATURE[1]),
                str(self.remove_erroneous_values_from_T_plot),
            ]
            self.main_software_thread_log_message('INFO', f"Command to plot data: {command}")
            process = subprocess.Popen(command)
            #print("Subprocess started and main program continues...")
            
        if self.current_button == "plotToday_temp_T_btn":
            script_dir = os.path.dirname(os.path.abspath(__file__))
            python_executable = self.get_python_executable()
            plot_script = os.path.join(script_dir, "appInteractivePlots.py") 
            command = [
                python_executable,
                plot_script,
                "PLOT_CURRENT_DAY_DATA_TEMPERATURES",
                str(self.VALID_RANGE_TEMPERATURE[0]),
                str(self.VALID_RANGE_TEMPERATURE[1]),
                str(self.remove_erroneous_values_from_T_plot),
            ]
            self.main_software_thread_log_message('INFO', f"Command to plot data: {command}")
            process = subprocess.Popen(command)
            #print("Subprocess started and main program continues...")
            
        if self.current_button == "plotAllDays_humidity_H_btn":
            script_dir = os.path.dirname(os.path.abspath(__file__))
            python_executable = self.get_python_executable()
            plot_script = os.path.join(script_dir, "appInteractivePlots.py") 
            command = [
                python_executable,
                plot_script,
                "PLOT_ALL_DAYS_DATA_HUMIDITY",
                str(self.VALID_RANGE_HUMIDITY[0]),
                str(self.VALID_RANGE_HUMIDITY[1]),
                str(self.remove_erroneous_values_from_H_plot),
            ]
            self.main_software_thread_log_message('INFO', f"Command to plot data: {command}")
            process = subprocess.Popen(command)
            #print("Subprocess started and main program continues...")
            
        if self.current_button == "plotToday_humidity_H_btn":
            script_dir = os.path.dirname(os.path.abspath(__file__))
            python_executable = self.get_python_executable()
            plot_script = os.path.join(script_dir, "appInteractivePlots.py") 
            command = [
                python_executable,
                plot_script,
                "PLOT_CURRENT_DAY_DATA_HUMIDITY",
                str(self.VALID_RANGE_HUMIDITY[0]),
                str(self.VALID_RANGE_HUMIDITY[1]),
                str(self.remove_erroneous_values_from_T_plot),
            ]
            self.main_software_thread_log_message('INFO', f"Command to plot data: {command}")
            process = subprocess.Popen(command)
            #print("Subprocess started and main program continues...")
            
        if self.current_button == "plotAllDays_cnt_H_btn":
            script_dir = os.path.dirname(os.path.abspath(__file__))
            python_executable = self.get_python_executable()
            plot_script = os.path.join(script_dir, "appInteractivePlots.py") 
            command = [
                python_executable,
                plot_script,
                "PLOT_ALL_DAYS_DATA_HUMIDIFIER",
                str(0),
                str(1),
                str(False),
            ]
            self.main_software_thread_log_message('INFO', f"Command to plot data: {command}")
            process = subprocess.Popen(command)
            #print("Subprocess started and main program continues...")
            
        if self.current_button == "plotToday_cnt_H_btn":
            script_dir = os.path.dirname(os.path.abspath(__file__))
            python_executable = self.get_python_executable()
            plot_script = os.path.join(script_dir, "appInteractivePlots.py") 
            command = [
                python_executable,
                plot_script,
                "PLOT_CURRENT_DAY_DATA_HUMIDIFIER",
                str(0),
                str(1),
                str(False),
            ]
            self.main_software_thread_log_message('INFO', f"Command to plot data: {command}")
            process = subprocess.Popen(command)
            #print("Subprocess started and main program continues...")
            
    def get_python_executable(self):
        """
        Searches the current script directory for a folder containing 'venv'
        (including hidden ones like '.venv') and returns the path to its python3 executable if found.
        Otherwise, returns sys.executable to preserve the current interpreter.
        """
        import os, sys

        script_dir = os.path.dirname(os.path.abspath(__file__))
        venv_dir = None

        for entry in os.listdir(script_dir):
            full_path = os.path.join(script_dir, entry)
            if os.path.isdir(full_path) and "venv" in entry:
                venv_dir = full_path
                break

        if venv_dir:
            python_path = os.path.join(venv_dir, "bin", "python3")
            if os.path.exists(python_path):
                return python_path

        # Fallback: use the currently running Python (which might already be in a venv)
        return sys.executable
        
    def handle_float_spinBox_value(self, spinbox_name, value):
        rounded_value = round(value, 1)
        self.main_software_thread_log_message('INFO', f"[MainSoftwareThread] Processing spinbox {spinbox_name} of value: {rounded_value} ({type(rounded_value)})")
        self.spinbox_values[spinbox_name] = rounded_value       
        
        if spinbox_name == "rotation_interval_spinBox":
            self.eggTurnerMotor.setFunctionInterval(rounded_value * 60) # Set the rotation interval
            self.save_parameter('ROTATION_INTERVAL', rounded_value)

        elif spinbox_name == "maxHysteresisValue_temperature_spinBox":
            if rounded_value <= self.thc.get_lower_limit():
                self.thc.set_upper_limit(rounded_value)
                self.save_parameter('TEMPERATURE_HYSTERESIS_CONTROLLER_UPPER_LIMIT', rounded_value)

                self.thc.set_lower_limit(rounded_value)
                self.save_parameter('TEMPERATURE_HYSTERESIS_CONTROLLER_LOWER_LIMIT', rounded_value)
                self.update_spinbox_value.emit("minHysteresisValue_temperature_spinBox", rounded_value)
            else:
                self.thc.set_upper_limit(rounded_value)
                self.save_parameter('TEMPERATURE_HYSTERESIS_CONTROLLER_UPPER_LIMIT', rounded_value)
                
        elif spinbox_name == "minHysteresisValue_temperature_spinBox":
            if rounded_value >= self.thc.get_upper_limit():
                self.thc.set_upper_limit(rounded_value)
                self.save_parameter('TEMPERATURE_HYSTERESIS_CONTROLLER_UPPER_LIMIT', rounded_value)

                self.thc.set_lower_limit(rounded_value)
                self.save_parameter('TEMPERATURE_HYSTERESIS_CONTROLLER_LOWER_LIMIT', rounded_value)
                self.update_spinbox_value.emit("maxHysteresisValue_temperature_spinBox", rounded_value)
            else:
                self.thc.set_lower_limit(rounded_value)
                self.save_parameter('TEMPERATURE_HYSTERESIS_CONTROLLER_LOWER_LIMIT', rounded_value)
                
        elif spinbox_name == "maxHysteresisValue_humidity_spinBox":
            if rounded_value <= self.hhc.get_lower_limit():
                self.hhc.set_upper_limit(rounded_value)
                self.save_parameter('HUMIDITY_HYSTERESIS_CONTROLLER_UPPER_LIMIT', rounded_value)

                self.hhc.set_lower_limit(rounded_value)
                self.save_parameter('HUMIDITY_HYSTERESIS_CONTROLLER_LOWER_LIMIT', rounded_value)
                self.update_spinbox_value.emit("minHysteresisValue_humidity_spinBox", rounded_value)
            else:
                self.hhc.set_upper_limit(rounded_value)
                self.save_parameter('HUMIDITY_HYSTERESIS_CONTROLLER_UPPER_LIMIT', rounded_value)
            self.publish_humidity_setpoint()

        elif spinbox_name == "minHysteresisValue_humidity_spinBox":
            if rounded_value >= self.hhc.get_upper_limit():
                self.hhc.set_upper_limit(rounded_value)
                self.save_parameter('HUMIDITY_HYSTERESIS_CONTROLLER_UPPER_LIMIT', rounded_value)

                self.hhc.set_lower_limit(rounded_value)
                self.save_parameter('HUMIDITY_HYSTERESIS_CONTROLLER_LOWER_LIMIT', rounded_value)
                self.update_spinbox_value.emit("maxHysteresisValue_humidity_spinBox", rounded_value)
            else:
                self.hhc.set_lower_limit(rounded_value)
                self.save_parameter('HUMIDITY_HYSTERESIS_CONTROLLER_LOWER_LIMIT', rounded_value)
            self.publish_humidity_setpoint()

        elif spinbox_name == "setPointHumidity_spinBox":
            self.apply_humidity_setpoint(rounded_value)

        elif spinbox_name == "maxHysteresisValue_waterLevelControl_spinBox":
            if rounded_value <= self.whc.get_lower_limit():
                self.whc.set_upper_limit(rounded_value)
                self.save_parameter('WATER_LEVEL_CONTROL_HYSTERESIS_CONTROLLER_UPPER_LIMIT', rounded_value)

                self.whc.set_lower_limit(rounded_value)
                self.save_parameter('WATER_LEVEL_CONTROL_HYSTERESIS_CONTROLLER_LOWER_LIMIT', rounded_value)
                self.update_spinbox_value.emit("minHysteresisValue_waterLevelControl_spinBox", rounded_value)
            else:
                self.whc.set_upper_limit(rounded_value)
                self.save_parameter('WATER_LEVEL_CONTROL_HYSTERESIS_CONTROLLER_UPPER_LIMIT', rounded_value)
            # Eco verso la view: senza questo le linee dei limiti sul disegno del
            # serbatoio (e lo stato inviato a un browser che si ricollega) restano
            # ferme al valore caricato all'avvio da parameters.json.
            self.update_spinbox_value.emit("maxHysteresisValue_waterLevelControl_spinBox", rounded_value)


        elif spinbox_name == "minHysteresisValue_waterLevelControl_spinBox":
            if rounded_value >= self.whc.get_upper_limit():
                self.whc.set_upper_limit(rounded_value)
                self.save_parameter('WATER_LEVEL_CONTROL_HYSTERESIS_CONTROLLER_UPPER_LIMIT', rounded_value)

                self.whc.set_lower_limit(rounded_value)
                self.save_parameter('WATER_LEVEL_CONTROL_HYSTERESIS_CONTROLLER_LOWER_LIMIT', rounded_value)
                self.update_spinbox_value.emit("maxHysteresisValue_waterLevelControl_spinBox", rounded_value)
            else:
                self.whc.set_lower_limit(rounded_value)
                self.save_parameter('WATER_LEVEL_CONTROL_HYSTERESIS_CONTROLLER_LOWER_LIMIT', rounded_value)
            # Eco verso la view (vedi nota sul limite massimo qui sopra)
            self.update_spinbox_value.emit("minHysteresisValue_waterLevelControl_spinBox", rounded_value)


        elif spinbox_name == "setPointTemperature_PID_spinBox":
            self.pid_temperature.set_reference_value(rounded_value)
            self.save_parameter('TEMPERATURE_PID_SET_POINT', rounded_value)
            # Eco verso la view: senza questo il browser non sa che il setpoint è
            # cambiato e continua a calcolare il "Δ from setpoint" sul valore
            # caricato all'avvio da parameters.json.
            self.update_spinbox_value.emit("setPointTemperature_PID_spinBox", rounded_value)
        elif spinbox_name == "Kp_spinBox":
            self.pid_temperature.set_gain_Kp(rounded_value)
            self.save_parameter('TEMPERATURE_PID_KP_GAIN', rounded_value)
        elif spinbox_name == "Ki_spinBox":
            computed_value = rounded_value * (10 ** (-self.multiplier))
            self.pid_temperature.set_gain_Ki(computed_value)
            self.save_parameter('TEMPERATURE_PID_KI_GAIN', rounded_value) 
            # NOTA: il valore salvato è in unità normali, tipo 9 o 10. Ma il valore settato in PID è *1e-3! perché per il sistema termico ci vogliono valori molto più piccoli
        elif spinbox_name == "Kd_spinBox":
            self.pid_temperature.set_gain_Kd(rounded_value)
            self.save_parameter('TEMPERATURE_PID_KD_GAIN', rounded_value)
        elif spinbox_name == "days_duration_spinBox":
            self.incubation_duration_days = int(value)
            self.save_parameter('INCUBATION_DURATION_DAYS', int(value))
            self.update_incubation_state()

        elif spinbox_name == "webRefreshInterval_spinBox":
            # Solo per il browser: quanto spesso la pagina ridisegna i dati.
            # Arduino e MainSoftwareThread continuano a girare alla stessa
            # velocità di sempre, non c'è nessun controllore da aggiornare qui.
            clamped_value = int(max(250, min(10000, value)))
            self.save_parameter('WEB_REFRESH_INTERVAL_MS', clamped_value)
            self.update_spinbox_value.emit("webRefreshInterval_spinBox", clamped_value)

        elif spinbox_name == "fanSpeed_spinBox":
            clamped_value = max(0.0, min(100.0, rounded_value))
            self.fan_speed_percent = clamped_value
            self.save_parameter('FAN_SPEED_PERCENT', clamped_value)
            self.queue_command("FAN01", round(clamped_value / 100.0, 3))
            # Eco verso la view: senza questo un secondo browser collegato non
            # vede il valore aggiornato finché non ricarica la pagina.
            self.update_spinbox_value.emit("fanSpeed_spinBox", clamped_value)

    def get_humidity_setpoint(self):
        """
        L'umidità è regolata da un controllore a isteresi, quindi non ha un
        setpoint proprio: il valore di riferimento è il centro della banda
        min/max. Ricavarlo invece di salvarlo a parte evita che i due valori
        possano divergere.
        """
        return round((self.hhc.get_upper_limit() + self.hhc.get_lower_limit()) / 2.0, 1)

    def publish_humidity_setpoint(self):
        """Riallinea la vista dopo una modifica dei limiti di isteresi."""
        setpoint = self.get_humidity_setpoint()
        self.spinbox_values["setPointHumidity_spinBox"] = setpoint
        self.update_spinbox_value.emit("setPointHumidity_spinBox", setpoint)

    def apply_humidity_setpoint(self, setpoint):
        """
        Sposta la banda di isteresi mantenendone l'ampiezza, centrata sul nuovo
        setpoint: dalla dashboard si regola l'umidità con un solo valore e la
        pagina Water & Humidity resta allineata.
        """
        half_width = (self.hhc.get_upper_limit() - self.hhc.get_lower_limit()) / 2.0
        half_width = min(max(half_width, 0.0), 50.0)   # banda sempre dentro 0–100 %
        setpoint = round(min(max(setpoint, half_width), 100.0 - half_width), 1)
        upper = round(setpoint + half_width, 1)
        lower = round(setpoint - half_width, 1)

        # Si sposta per primo il limite che si allontana dall'altro: impostandoli
        # nell'ordine sbagliato la banda passa per un istante da collassata
        # (upper == lower) e HysteresisController forza l'uscita a OFF.
        if lower < self.hhc.get_lower_limit():
            self.hhc.set_lower_limit(lower)
            self.hhc.set_upper_limit(upper)
        else:
            self.hhc.set_upper_limit(upper)
            self.hhc.set_lower_limit(lower)
        self.save_parameter('HUMIDITY_HYSTERESIS_CONTROLLER_UPPER_LIMIT', upper)
        self.save_parameter('HUMIDITY_HYSTERESIS_CONTROLLER_LOWER_LIMIT', lower)

        self.spinbox_values["setPointHumidity_spinBox"] = setpoint
        self.update_spinbox_value.emit("setPointHumidity_spinBox", setpoint)
        self.update_spinbox_value.emit("maxHysteresisValue_humidity_spinBox", upper)
        self.update_spinbox_value.emit("minHysteresisValue_humidity_spinBox", lower)

    def handle_intialization_step(self, value_name, value):
        rounded_value = round(value, 1)
        if value_name == "maxHysteresisValue_temperature_spinBox":
            self.thc.set_upper_limit(rounded_value)
        elif value_name == "minHysteresisValue_temperature_spinBox":
            self.thc.set_lower_limit(rounded_value)
        elif value_name == "maxHysteresisValue_humidity_spinBox":
            self.hhc.set_upper_limit(rounded_value)
        elif value_name == "minHysteresisValue_humidity_spinBox":
            self.hhc.set_lower_limit(rounded_value)
        elif value_name == "setPointHumidity_spinBox":
            # Derivato dalla banda di isteresi, che i due valori qui sopra hanno
            # già impostato: nulla da applicare al controllore.
            pass
        elif value_name == "maxHysteresisValue_waterLevelControl_spinBox":
            self.whc.set_upper_limit(rounded_value)
        elif value_name == "minHysteresisValue_waterLevelControl_spinBox":
            self.whc.set_lower_limit(rounded_value)
        elif value_name == "setPointTemperature_PID_spinBox":
            self.pid_temperature.set_reference_value(rounded_value)
        elif value_name == "Kp_spinBox":
            self.pid_temperature.set_gain_Kp(rounded_value)
        elif value_name == "Ki_spinBox":
            computed_value = rounded_value * (10 ** (-self.multiplier))
            self.pid_temperature.set_gain_Ki(computed_value)
        elif value_name == "Kd_spinBox":
            self.pid_temperature.set_gain_Kd(rounded_value)
        elif value_name == "days_duration_spinBox":
            self.incubation_duration_days = value
    
    def handle_initialization_done(self, parameter, value):
        # funzione che viene chiamata da MainWindow quando ha completato l'emit di tutti i parametri da default di GUI
        self.initialization_from_GUI_completed = True
        
    def on_date_received(self, name, value):
        if name == "incubationStartDate":
            self.incubation_start_date = value
            self.save_parameter('INCUBATION_START_DATE', self.incubation_start_date.isoformat())
            """
                Questo è come viene salvato: "INCUBATION_START_DATE": "2025-12-26"
            """
            # devo anche aggiornare la visualizzazione della start date!
            self.update_date_edit.emit("startDate_dateTimeBox", self.incubation_start_date)
            self.update_incubation_state()
            
    def handle_radio_button_toggle(self, value_name, value):
        if value_name == "heaterOFF_radioBtn":
            self.thc.set_control_mode("forceOFF")
        if value_name == "heaterAUTO_radioBtn":
            self.thc.set_control_mode("AUTO")
        if value_name == "heaterON_radioBtn":
            self.thc.set_control_mode("forceON")
            
        if value_name == "humidifierOFF_radioBtn":
            self.hhc.set_control_mode("forceOFF")
        if value_name == "humidifierAUTO_radioBtn":
            self.hhc.set_control_mode("AUTO")
        if value_name == "humidifierON_radioBtn":
            self.hhc.set_control_mode("forceON")
            
        if value_name == "evalveOFF_radioBtn":
            self.whc.set_control_mode("forceOFF")
        if value_name == "evalveAUTO_radioBtn":
            self.whc.set_control_mode("AUTO")
        if value_name == "evalveON_radioBtn":
            self.whc.set_control_mode("forceON")
            
        if value_name == "removeErrors_from_T_plots":
            self.remove_erroneous_values_from_T_plot = value
            self.main_software_thread_log_message('INFO', f"{value_name} + {value}")
            
        if value_name == "removeErrors_from_H_plots":
            self.remove_erroneous_values_from_H_plot = value
            self.main_software_thread_log_message('INFO', f"{value_name} + {value}")
            
        if value_name == "hysteresisActive_radioBtn":
            self.pid_temperature_is_activated = False
            self.save_parameter('PID_HEATING_MODE_IS_SELECTED', self.pid_temperature_is_activated)
            # Il relè torna a seguire il controllore: dimentico lo stato "già inviato"
            # e sblocco il debounce, così il prossimo ciclo riallinea l'attuatore.
            self._debounced_heater_output_thc = None
            self._last_output_change_time_thc = 0.0

        if value_name == "PIDActive_radioBtn":
            self.pid_temperature_is_activated = True
            self.save_parameter('PID_HEATING_MODE_IS_SELECTED', self.pid_temperature_is_activated)
            # Da qui in poi HTR01 non viene più inviato (vedi process_serial_data):
            # spengo il relè una volta sola, altrimenti resterebbe acceso all'infinito.
            self.queue_command("HTR01", False)
            self._debounced_heater_output_thc = False
            
            
    def process_serial_data(self, new_data):
        arduino_time_difference = time.time() - self.arduino_readings_timestamp
        self.arduino_readings_timestamp = time.time()
        # Convert to milliseconds and format without commas
        ms = int(arduino_time_difference * 1000)
        self.main_software_thread_log_message('INFO', f"Data from serial now! Time passed wrt previous data: {ms} ms", True) # suppress terminal print
        
        self.current_data = new_data
        #print(new_data)
        # [{'TMP01': 19.5}, {'TMP02': 19.1}, {'TMP03': 19.8}, {'TMP04': 20.1}, {'HUM01': 19.5}, {'HTP01': 19.5}]
        # Extract data into specific categories - questi che seguono sono tutti dictionaries
        current_temperatures = {k: v for d in new_data for k, v in d.items() if k.startswith("TMP")} # {'TMP01': 22.3, 'TMP02': 22.2, 'TMP03': 22.3, 'TMP04': 22.4}
        current_humidities = {k: v for d in new_data for k, v in d.items() if k.startswith("HUM")}
        current_humidities_temperatures = {k: v for d in new_data for k, v in d.items() if k.startswith("HTP")}
        current_inductors_feedbacks = {k: v for d in new_data for k, v in d.items() if k.startswith("IND")} #{'IND_CCW': 1, 'IND_CW': 1}
        current_external_temperature = {k: v for d in new_data for k, v in d.items() if k.startswith("EXTT")} #{'EXTT': 25.2}
        current_weight = {k: v for d in new_data for k, v in d.items() if k.startswith("WGT")} 
        
        '''
        Mi dimentico sempre il formato dei dati, ecco un print:
        #print(list(current_temperatures.values()))  [11.9, 11.6, 11.3, 11.1]
        #print(current_temperatures) {'TMP01': 11.9, 'TMP02': 11.6, 'TMP03': 11.3, 'TMP04': 11.1}
        #print(type(current_temperatures)) <class 'dict'>
        '''
        
        
        # TEMPERATURE CONTROLLER SECTION
        # faccio l'update qui: ogni votla che arrivano dati nuovi li elaboro, anche nel controllore
        # al controllore di temperatura passo solo temperature filtrate, ovvero i valori dentro il range di temperatura corretto
        
        '''
            Un frame seriale può non contenere alcuna lettura di temperatura: Arduino
            accoda gli eventi degli induttori (<IND_CW,1>/<IND_CCW,1>) in qualunque
            iterazione del loop, mentre il blocco sensori lo aggiunge solo quando la
            conversione dei DS18B20 è pronta. Il risultato è un frame tipo @<IND_CW, 1>#
            con current_temperatures vuoto.

            ATTENZIONE alla distinzione, perché i due casi richiedono reazioni opposte:
              - nessuna temperatura NEL FRAME  -> frame parziale, NON è un guasto:
                i controllori non vanno aggiornati, l'attuazione resta com'è
              - temperature presenti ma tutte fuori range -> guasto sensori:
                si spegne l'attuatore, esattamente come prima

            Senza questa distinzione ogni evento induttore faceva passare una lista
            vuota ai controllori, che la interpretano come guasto e spengono l'uscita
            (ramo forceOFF del PID e "if not values" di HysteresisController), con
            comandi spuri all'attuatore (PWM a 0 e ritorno a 1.0 un secondo dopo).
        '''
        temperatures_in_frame = bool(current_temperatures)

        filtered_temperatures = self.filter_temperatures(list(current_temperatures.values()))
        if temperatures_in_frame and not filtered_temperatures:
            self.main_software_thread_log_message('WARNING', 'filtered_temperature list is empty! fault in the sensors')

        if self.pid_temperature_is_activated and temperatures_in_frame:
            # === PID Controller with Automatic Timing === #
            # nel caso di controllo ad isteresi avevo fatto tutto internamente...ma qui PID rimane general purpose. Il setpoint è uno e calcolato fuori dal PID.

            # Ensure values is a list for consistency
            if not isinstance(filtered_temperatures, list):
                _values = [filtered_temperatures]
            else:
                _values = filtered_temperatures
            
            if not _values:
                self.pid_temperature.set_control_mode("forceOFF") # FOR SAFETY!! no values in input, means no good temperatures are passed, then OFF the actuator
                _mean_value = 0.0 # va popolato comuque! perché viene usato dopo...
            else:   
                self.pid_temperature.set_control_mode("AUTO")                    
                _mean_value = round(sum(_values) / len(_values), 1) # Calculate the mean of the values

            ext_temp = current_external_temperature.get("EXTT")
            if ext_temp is not None:
                self.pid_temperature.update_ambient(ext_temp)
                
            updated = self.pid_temperature.update(current_value_loc=_mean_value, dt_threshold=self.Ts)
            if updated:
                """
                    Ricorda che se per qualche motivo il PID è settato forceOFF ok, _mean_value non verrà aggiornato, ma la classe del PID non considera più quel valore, perché
                    forza a 0 l'uscita e fa return diretto.
                    # PID update will only actually compute if at least dt_threshold seconds have passed since the last update. This method returns True when update is done
                    In questo modo abbiamo un comando mandato verso arduino una volta ogni 2 secondi. SIcuramente sufficiente per calcolare correttametne l'output, considerando le dinamiche di temperatura
                """
                # Arduino-friendly PWM value
                #self.pwm = self.pid_temperature.get_output_for_arduino() #[0..255]
                self.pwm = self.pid_temperature.get_normalized_output() #[0..1]
                '''
                print(
                      f"Kp {self.pid_temperature.kp}"  
                      f" Ki {self.pid_temperature.ki}"
                      f" Integral action: {self.pid_temperature.integral}"  
                      f" Output: {self.pid_temperature.output}"
                      f" ff: {self.pid_temperature.ff}"                      
                      )
                '''
                #print(f"{self.pwm}")

            PWM_DELTA_THRESHOLD = 0.005   # soglia di variazione minima - robustezza alla variazione per i FLOAT

            '''
                Il comando non va inviato SOLO quando cambia: u_cmd sull'Arduino è una
                variabile globale che un riavvio della scheda riporta a 0.0. Con il PID
                in saturazione (uscita ferma a 1.0) il comando non cambiava mai, quindi
                dopo un reset il riscaldatore restava spento all'infinito mentre l'interfaccia
                continuava a mostrare duty 100%. Il rinvio periodico riallinea la scheda
                e alimenta il failsafe FAILSAFE_MS lato firmware (se non arrivano comandi
                per 30 s l'Arduino azzera l'uscita da solo).
            '''
            now_pwm = time.time()
            pwm_value_changed = abs(self.pwm - self.last_pwm_sent) >= PWM_DELTA_THRESHOLD
            pwm_refresh_due = (now_pwm - self._last_pwm_sent_time) >= self.PWM_RESEND_INTERVAL_SEC

            if pwm_value_changed or pwm_refresh_due:
                self.queue_command("PWM01", self.pwm)
                self.main_software_thread_log_message(
                    'INFO',
                    f"⚙️ Heater PWM value sent: {self.pwm}" if pwm_value_changed
                    else f"⚙️ Heater PWM refresh: {self.pwm}",
                    not pwm_value_changed  # il refresh periodico non sporca il terminale
                )
                self.last_pwm_sent = self.pwm
                self._last_pwm_sent_time = now_pwm
            '''
            # in questo modo inviamo ad Arduino un comando al secondo....dovrebbe essere ok da gestire.
            self.queue_command("PWM01", self.pwm)
            self.main_software_thread_log_message('INFO', f"⚙️ Heater PWM value sent: {self.pwm}")
            '''

        # === Hysteresis temperature controller is active === #
        ''' NOTA: lui cicla sempre!
        1) perché così a video vedo cosa farebbe lui/vedo la sua temperatura di controllo aggiornarsi col valore medio
        2) posso usare i comandi di force ON/OFF, utile questa cosa per fare l'identificazione del modello termico dell'incubatrice 
            (a patto di attivare PID + cablare heater sull'uscita del RELE' e non dell'SSR)
            2.1) questo mi permette di loggare nel file quando va/non va il riscaldatore e quindi fare opportuna identificazione del modello
        '''
        if temperatures_in_frame:
            self.thc.update(filtered_temperatures)
        # NB: il debounce qui sotto legge thc.get_output_control(): saltando l'update
        # l'uscita resta invariata, quindi non parte nessun comando HTR01 spurio.
        #print(list(current_temperatures.values()))  [11.9, 11.6, 11.3, 11.1]
        #print(current_temperatures) {'TMP01': 11.9, 'TMP02': 11.6, 'TMP03': 11.3, 'TMP04': 11.1}
        #print(type(current_temperatures)) <class 'dict'>

        '''
            chatGPT: adding debounce logic.
            La scelta migliore è implementarlo nel codice che controlla l’output dell’heater, non all’interno della classe Heater
            La classe Heater dovrebbe occuparsi solo del controllo logico / PID.
            La decisione di inviare o meno un comando sulla seriale è una responsabilità applicativa, 
            quindi deve stare nel codice che usa Heater, cioè fuori da essa, per mantenere una buona separazione delle responsabilità (principio SOLID: Single Responsibility).
        '''
        current_state = self.thc.get_output_control()

        if current_state != self._last_output_state_thc:
            self._last_output_change_time_thc = time.time()
            self._last_output_state_thc = current_state

        '''
            In modo PID il calore lo fa l'SSR pilotato da PWM01: il relè 1 (HTR01) è
            ridondante. Ogni sua eccitazione però resetta l'Arduino - misurato: 39 comandi
            HTR01 True, 39 silenzi di ~2.44 s (tempo di riavvio della scheda) e 38 NO ACK,
            zero fallimenti su HTR01 False / PWM01 / ELV01 - quindi in modo PID non va
            proprio inviato. Il controllore a isteresi continua comunque a girare, così
            la sua uscita resta visibile in interfaccia e nelle statistiche.
        '''
        # Se è cambiato e il nuovo stato è stabile da X secondi
        if (
            not self.pid_temperature_is_activated and
            self._last_output_state_thc != self._debounced_heater_output_thc and
            self._last_output_change_time_thc is not None and
            (time.time() - self._last_output_change_time_thc) >= self._debounce_duration
        ):
            self._debounced_heater_output_thc = self._last_output_state_thc
            self.queue_command("HTR01", self._debounced_heater_output_thc)
            self.main_software_thread_log_message('INFO', f"⚙️ Debounced Heater state sent: {self._debounced_heater_output_thc}")

        # Check for persistent errors
        self.check_errors(current_temperatures)               
            
            
        # HUMIDITY CONTROLLER SECTION
        # Stessa distinzione fatta per la temperatura: un frame senza letture di umidità
        # è un frame parziale, non un guasto del sensore.
        if current_humidities:
            self.hhc.update(list(current_humidities.values()))
        current_state = self.hhc.get_output_control()

        if current_state != self._last_output_state_hhc:
            self._last_output_change_time_hhc = time.time()
            self._last_output_state_hhc = current_state

        # Se è cambiato e il nuovo stato è stabile da X secondi
        if (
            self._last_output_state_hhc != self._debounced_heater_output_hhc and
            self._last_output_change_time_hhc is not None and
            (time.time() - self._last_output_change_time_hhc) >= self._debounce_duration
        ):
            self._debounced_heater_output_hhc = self._last_output_state_hhc
            self.queue_command("HUMER01", self._debounced_heater_output_hhc)
            self.main_software_thread_log_message('INFO', f"⚙️ Debounced Humidifier state sent: {self._debounced_heater_output_hhc}")
        
        # WATER LEVEL CONTROL - CONTROLLER SECTION        
        #weights_kg = [round(w / 1000, 1) for w in list(current_weight.values())] # from grams to kg   - ROUNDING - non mi piace molto
        weights_kg = [math.trunc(w / 1000 * 10) / 10 for w in current_weight.values()]
        
        saturated_weights = self.saturate_values(weights_kg, self.VALID_RANGE_WATER_LEVEL)
        # Idem: senza lettura della cella di carico non si tocca l'elettrovalvola.
        if weights_kg:
            self.whc.update(saturated_weights)
        current_state = self.whc.get_output_control()

        if current_state != self._last_output_state_whc:
            self._last_output_change_time_whc = time.time()
            self._last_output_state_whc = current_state

        # Se è cambiato e il nuovo stato è stabile da X secondi
        if (
            self._last_output_state_whc != self._debounced_heater_output_whc and
            self._last_output_change_time_whc is not None and
            (time.time() - self._last_output_change_time_whc) >= self._debounce_duration
        ):
            self._debounced_heater_output_whc = self._last_output_state_whc
            self.queue_command("ELV01", self._debounced_heater_output_whc)
            self.main_software_thread_log_message('INFO', f"⚙️ Debounced Water Electro-valve state sent: {self._debounced_heater_output_whc}")    
            
        # Emit the data to update the view        
        # Collecting all values into a single list
        # questo all_values è semplicemente una lista [17.8, 17.9, 18.0, 17.9, 17.8, 17.9] dove SO IO ad ogni posto cosa è associato...passiamo solo i valori (non bellissimo...)
        # i mean value servono per pubblicare il valore che il controllore usa per fare effettivamente il controllo e lo metto nella sezione di isteresi
        # list() se passi un dizionario, mentre [] se vuoi aggiugnere alla lista elementi singoli
        if current_temperatures and current_humidities and weights_kg:
            _temperature_references = self.get_temperature_references()
            # all_values for MAIN VIEW page
            all_values = (
                list(current_temperatures.values()) +               # [0 1 2 3] Temperature interne
                list(current_humidities.values()) +                 # [4] Umidità interna
                list(current_humidities_temperatures.values()) +    # [5] Umidità/temperatura combinate
                [self.thc.get_mean_value()] +                       # [6] Media THC
                [self.hhc.get_mean_value()] +                       # [7] Media HHC
                [self.thc.get_output_control()] +                   # [8] Output controllo THC
                [self.hhc.get_output_control()] +                   # [9] Output controllo HHC
                list(current_external_temperature.values()) +       # [10] Temperature esterne
                weights_kg +                                        # [11] Peso totale o lista pesi kg
                [self.whc.get_mean_value()] +                       # [12] Media WHC
                [self.whc.get_output_control()] +                   # [13] Output controllo WHC
                [self.pid_temperature.get_current_value()] +        # [14] Valore di controllo usato dal PIDController per fare i conti
                [
                    self.pwm if self.pid_temperature_is_activated else 0.0
                ] +                                                 # [15] Valore PWM del sistema per arduino
                [
                    _temperature_references['SETPOINT'],             # [16] Setpoint del PID
                    _temperature_references['SP_MIN'],               # [17] Limite inferiore isteresi
                    _temperature_references['SP_MAX'],               # [18] Limite superiore isteresi
                ]
            )
                        
            self.update_view.emit(all_values)
            
            # all_values for STATISTICS page
            all_values = []
            all_values.append(self.thc.get_min_value())
            all_values.append(self.thc.get_mean_value())
            all_values.append(self.thc.get_max_value())
            all_values.append(self.thc.get_on_count())
            all_values.append(self.thc.get_off_count())
            all_values.append(self.thc.get_time_on())
            all_values.append(self.thc.get_time_off())
            
            all_values.append(self.hhc.get_min_value())
            all_values.append(self.hhc.get_mean_value())
            all_values.append(self.hhc.get_max_value())
            all_values.append(self.hhc.get_on_count())
            all_values.append(self.hhc.get_off_count())
            all_values.append(self.hhc.get_time_on())
            all_values.append(self.hhc.get_time_off())
            
            self.update_statistics.emit(all_values)

            # + saving in parameters file relevan statistics
            # In modo PID il relè HTR01 non viene mai comandato (vedi la nota sopra,
            # nella sezione del controllore a isteresi): i suoi tempi di ON/OFF
            # contano un'accensione che nella realtà non avviene, quindi non c'è
            # niente da conservare. Saltando il salvataggio si evita anche di
            # riscrivere parameters.json a ogni frame seriale, che su microSD è
            # I/O continuo e consuma la scheda.
            if not self.pid_temperature_is_activated:
                self.save_parameter('TEMPERATURE_HYSTERESIS_CONTROLLER_TIME_ON', self.thc.get_time_on())
                self.save_parameter('TEMPERATURE_HYSTERESIS_CONTROLLER_TIME_OFF', self.thc.get_time_off())
        
        # INDUCTOR SECTION
        """
            IND_CCW - IND_CW induttori posizioni limite counterClockWise - clockWise
            IND_HOR induttore posizionamento orizzontale
            IND_DR door (apertura/chiusura della porta)
        """
        if current_inductors_feedbacks: # that is checking if the dictionary is not empty
            #print(current_inductors_feedbacks)
            
            value = current_inductors_feedbacks.get("IND_CCW") # to handle cases where the key might not exist safely, you can use .get() - questa funzione tira fuori il valore, non la key!
            if value is not None and value == 1:
                # perché nella mia convenzione value == 1 significa rising edge della posizione ragginuta
                self.eggTurnerMotor.acknowledgeFromExternal("IND_CCW")
                
            value = current_inductors_feedbacks.get("IND_CW") # to handle cases where the key might not exist safely, you can use .get()
            if value is not None and value == 1:
                self.eggTurnerMotor.acknowledgeFromExternal("IND_CW")
        
        
        
             
        
        # SAVING DATA IN FILES
        time_difference = datetime.now() - self.last_saving_time
        
        if (time_difference >= timedelta(minutes = self.saving_interval)):
                start_time = time.perf_counter()

                # Un pacchetto seriale può arrivare incompleto (parsing parziale, ACK
                # mancante, riga troncata): in quel caso il dizionario corrispondente è
                # vuoto. Salvarlo lo stesso scriverebbe una riga con il solo timestamp,
                # quindi ogni scrittura è protetta dalla sua guardia e i dataset saltati
                # vengono elencati nel log, per poter risalire alla frequenza del problema.
                skipped = []

                if current_external_temperature:
                    self.save_data_to_files('External_Temperature', current_external_temperature) #{'EXTT': 25.2}
                else:
                    skipped.append('External_Temperature')

                if current_temperatures:
                    # Alle temperature misurate accodo i riferimenti di controllo, così rileggendo
                    # il CSV (grafici History) si vede subito a quale target puntava la macchina.
                    # SETPOINT = riferimento PID; SP_MIN/SP_MAX = banda del controllore a isteresi.
                    self.save_data_to_files('Temperatures', dict(
                        list(current_temperatures.items()) + list(self.get_temperature_references().items())
                    )) #{'TMP01': 23.1, ..., 'SETPOINT': 37.8, 'SP_MIN': 37.5, 'SP_MAX': 37.8}
                else:
                    skipped.append('Temperatures')

                if current_humidities:
                    self.save_data_to_files('Humidity', current_humidities) #{'HUM01': 52.5}
                else:
                    skipped.append('Humidity')

                if current_weight:
                    self.save_data_to_files('Water_Weight', current_weight)
                else:
                    skipped.append('Water_Weight')

                # Questi non vengono dal pacchetto seriale ma dallo stato dei controllori,
                # quindi sono sempre disponibili.
                self.save_data_to_files('Heater', {'Heater_Status': self.thc.get_output_control()})  # need to pass a dictionary
                self.save_data_to_files('Humidifier', {'Humidifier_status': self.hhc.get_output_control()})
                self.save_data_to_files('PID_Duty_Cycle', {'PID_Duty_Cycle': self.last_pwm_sent}) # need to pass a dictionary

                self.last_saving_time = datetime.now()
                if skipped:
                    self.main_software_thread_log_message(
                        'WARNING',
                        f"Pacchetto seriale incompleto: nessun dato per {', '.join(skipped)}, "
                        f"campione saltato. Dati ricevuti: {self.current_data}"
                    )
                self.main_software_thread_log_message('SAVING', f"Saved data! {self.last_saving_time}")
                
                
                end_time = time.perf_counter()
                #print(f"Time requested for saving data [milli-seconds]: {(end_time - start_time)*1000}")

        # GENERAL PURPOSE savings: may need to have a different saving time intervals
        '''
            For example, in case of thermal system identification procedure I need to probe the system temperature @ 0.1Hz - 1Hz, that is, in the most demanding case, 
            1x saving every second (that is much more frequent that once every minute of the previous case).
            So I need to use another branch.

        '''
        time_difference_generalPurposeSaving = datetime.now() - self.last_saving_time_generalPurposeSaving
        if (False and (time_difference_generalPurposeSaving >= timedelta(seconds = self.saving_interval_generalPurposeSaving))):
            
            # GENERAL_PURPOSE_1: IDENTIFICAZIONE DEL MODELLO TERMICO DELL'INCUBATRICE
            if self.pid_temperature_is_activated:
                ''' 
                    Questa parte mi serve per salvare i dati per fare l'identificazione del modello termico dell'incubatrice.
                    Avrò bisogno di salvare la temperatura media, che mi sono creato quando è attivo il PID. Allora, anziché ricostruirmela ancora, 
                    la prendo direttamente da lì. Per quello condiziono questa parte con lo stesso flag che crea quella variabile.                    
                    Se dovessi aver bisogno di altri general purpose, allora li farò dedicati volta per volta
                '''
                #{'PWR': 20.0, 'T': 23.1, 'Tamb': 12.5, 'HEATER_STATUS': 0}
                general_purpose_dict = {
                    "PWR": self.configured_heater_power,
                    "T": self.pid_temperature.get_current_value(),
                    "Tamb": current_external_temperature["EXTT"], # siccome sto creando un dict io a mano, qui devo estrarre il valore effettivo del sensore. Non è proprio uguale a sopra...
                    "HTR_STATE": self.thc.get_output_control()
                }
                
                '''
                    Formato di salvataggio del file:
                        Timestamp,PWR,T,Tamb,HTR_STATE
                        2025-12-14 10:16:54,0.158,0.0,11.8,False
                '''
                self.save_data_to_files('General_Purpose', general_purpose_dict)
                self.last_saving_time_generalPurposeSaving = datetime.now()
                self.main_software_thread_log_message('SAVING', f"General Purpose Saved data! {self.last_saving_time_generalPurposeSaving}")

    def write_log_section_header(self):
        """
        Writes a section header in the existing daily log file to mark the start of a new thread run.
        """
        current_date = datetime.now().strftime('%Y-%m-%d')
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log_file = os.path.join(self.main_software_thread_log_folder_path, f"log_{current_date}.txt")

        append_text_safe(
            log_file,
            "\n"
            "=====================================\n"
            f" New Run - {timestamp}\n"
            "=====================================\n"
        )

    def main_software_thread_log_message(self, error_type, message, suppress_terminal_print = False):
        """
        Logs a message to a log file named with the current date inside the Log folder.
        Format: [ERROR_TYPE] Message @ Timestamp

        Args:
            error_type (str): The type of error (e.g., 'INFO', 'WARNING', 'ERROR').
            message (str): The message to log.
        """
        if not suppress_terminal_print:
            print(message)
        
        current_date = datetime.now().strftime('%Y-%m-%d')
        log_file = os.path.join(self.main_software_thread_log_folder_path, f"log_{current_date}.txt")
        
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        append_text_safe(log_file, f"[{error_type}] {message} @ {timestamp}\n")
        '''
        OLD VERSION FOR JSON FORMAT

        """
        Logs a message to a log file named with the current date inside the Log folder in plain text format with sections.

        Args:
            error_type (str): The type of error (e.g., 'INFO', 'WARNING', 'ERROR').
            message (str): The message to log.
        """
        print(message)
        current_date = datetime.now().strftime('%Y-%m-%d')
        log_file = os.path.join(self.main_software_thread_log_folder_path, f"log_{current_date}.txt")
        
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        with open(log_file, 'a') as file:
            file.write(f"--- Log Entry ---\n")
            file.write(f"Timestamp: {timestamp}\n")
            file.write(f"Error Type: {error_type}\n")
            file.write(f"Message: {message}\n")
            file.write(f"-----------------\n\n")
        '''
    
    def _ensure_csv_header(self, file_path, wanted_header):
        """
        Allinea l'header del CSV alle colonne che stiamo per scrivere e
        restituisce l'header effettivamente presente nel file.

        Le colonne vengono SOLO aggiunte, mai rimosse: se un campione arriva
        incompleto (es. un pacchetto seriale senza temperature) le sue colonne
        mancanti restano vuote, ma quelle già nel file non vengono perse.
        Le righe preesistenti sono conservate, con celle vuote sulle colonne
        nuove (nei grafici appaiono come buchi).
        """
        with open_with_retry(file_path, 'r', newline='') as file:
            current_header = next(csv.reader(file), None)

        # File presente ma vuoto (es. creato e mai scritto)
        if current_header is None:
            with open_with_retry(file_path, 'w', newline='') as file:
                csv.writer(file).writerow(wanted_header)
            return list(wanted_header)

        merged_header = list(current_header) + [
            column for column in wanted_header if column not in current_header
        ]
        if merged_header == current_header:
            return current_header

        with open_with_retry(file_path, 'r', newline='') as file:
            old_rows = list(csv.DictReader(file))

        with open_with_retry(file_path, 'w', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(merged_header)
            for row in old_rows:
                writer.writerow([row.get(column) or '' for column in merged_header])

        self.main_software_thread_log_message(
            'INFO',
            f"CSV header aggiornato in {os.path.basename(file_path)}: "
            f"{current_header} -> {merged_header} ({len(old_rows)} righe mantenute)"
        )
        return merged_header

    def get_temperature_references(self):
        """
        Riferimenti di controllo della temperatura, nell'ordine in cui finiscono
        nel CSV e nei grafici:
          SETPOINT → setpoint del PID
          SP_MIN   → limite inferiore del controllore a isteresi
          SP_MAX   → limite superiore del controllore a isteresi
        Vengono restituiti sempre entrambi i riferimenti, indipendentemente da
        quale dei due controllori è attivo: quello attivo si legge da
        self.pid_temperature_is_activated.
        """
        def _num(value):
            try:
                return round(float(value), 2)
            except (TypeError, ValueError):
                return None

        return {
            'SETPOINT': _num(self.pid_temperature.get_reference_value()),
            'SP_MIN':   _num(self.thc.get_lower_limit()),
            'SP_MAX':   _num(self.thc.get_upper_limit()),
        }

    def save_data_to_files(self, data_type, data_dictionary): #passo un dictionary di temperature/humidities, dimensione variabile per gestire più o meno sensori dinamicamente
        now = datetime.now()
        current_date = now.strftime('%Y-%m-%d')
        
        script_dir = os.path.dirname(os.path.abspath(__file__))
        machine_statistics_folder_path = os.path.join(script_dir, "Machine_Statistics") 
        # faccio selezione del folder da cui pescare i dati.
        if data_type == 'External_Temperature':
            folder_path = os.path.join(machine_statistics_folder_path, 'External_Temperature')        
        elif data_type == 'Temperatures':
            folder_path = os.path.join(machine_statistics_folder_path, 'Temperatures')
        elif data_type == 'Humidity':
            folder_path = os.path.join(machine_statistics_folder_path, 'Humidity')
        elif data_type == 'Heater':
            folder_path = os.path.join(machine_statistics_folder_path, 'Heater')
        elif data_type == 'Humidifier':
            folder_path = os.path.join(machine_statistics_folder_path, 'Humidifier')
        elif data_type == 'Water_Weight':
            folder_path = os.path.join(machine_statistics_folder_path, 'Water_Weight')
        elif data_type == 'PID_Duty_Cycle':
            folder_path = os.path.join(machine_statistics_folder_path, 'PID_Duty_Cycle')
        elif data_type == 'General_Purpose':
            folder_path = os.path.join(machine_statistics_folder_path, 'General_Purpose')
        else:
            raise ValueError("Invalid path configuration")	
            
        file_path = os.path.join(folder_path, f"{current_date}.csv")

        timestamp = now.strftime('%Y-%m-%d %H:%M:%S')

        # Colonne che questo campione vorrebbe scrivere ('Timestamp' sempre per prima)
        wanted_header = ['Timestamp'] + list(data_dictionary.keys())

        # Il file è dentro OneDrive: una scrittura può fallire temporaneamente.
        # Perdere un campione è accettabile, far cadere il thread di controllo no.
        try:
            # Initialize the CSV file with headers if it doesn't exist
            if not os.path.exists(file_path):
                header = list(wanted_header)
                with open_with_retry(file_path, 'w', newline='') as file: # write mode
                    csv.writer(file).writerow(header)
            else:
                # Il file di oggi può essere stato creato da una versione del
                # software con colonne diverse (es. prima dell'aggiunta di
                # SETPOINT/SP_MIN/SP_MAX): in quel caso va riscritto l'header,
                # altrimenti le righe nuove risulterebbero disallineate.
                header = self._ensure_csv_header(file_path, wanted_header)

            # La riga viene composta seguendo l'header del file, non l'ordine del
            # dizionario: un campione con chiavi mancanti lascia celle vuote
            # invece di sfalsare le colonne.
            row = dict(data_dictionary)
            row['Timestamp'] = timestamp

            with open_with_retry(file_path, 'a', newline='') as file: # append mode
                csv.writer(file).writerow(
                    [row.get(column, '') if row.get(column) is not None else ''
                     for column in header]
                )
        except Exception as e:
            print(f"[SAVE-FAIL] {data_type}: impossibile scrivere {file_path}: {e}")

    # === GESTIONE PARAMETRI === #
    def parameters_initialization_from_file(self):
        '''
            TEMPERATURE_HYSTERESIS_CONTROLLER_UPPER_LIMIT
            TEMPERATURE_HYSTERESIS_CONTROLLER_LOWER_LIMIT
            HUMIDITY_HYSTERESIS_CONTROLLER_UPPER_LIMIT
            HUMIDITY_HYSTERESIS_CONTROLLER_LOWER_LIMIT
            WATER_LEVEL_CONTROL_HYSTERESIS_CONTROLLER_UPPER_LIMIT
            WATER_LEVEL_CONTROL_HYSTERESIS_CONTROLLER_LOWER_LIMIT
            TEMPERATURE_HYSTERESIS_CONTROLLER_TIME_ON
            TEMPERATURE_HYSTERESIS_CONTROLLER_TIME_OFF
            HUMIDITY_HYSTERESIS_CONTROLLER_TIME_ON
            HUMIDITY_HYSTERESIS_CONTROLLER_TIME_OFF
            TURNS_COUNTER
            ROTATION_INTERVAL
            TEMPERATURE_PID_SET_POINT
            TEMPERATURE_PID_KP_GAIN
            TEMPERATURE_PID_KI_GAIN
            TEMPERATURE_PID_KD_GAIN
            INCUBATION_START_DATE - isoformat d/M/yy
            INCUBATION_DURATION_DAYS - days
            PID_HEATING_MODE_IS_SELECTED - bool True/False
        '''
        # TEMPERATURE SPINBOX MIN/MAX
        thc_upper_limit = self.load_parameter('TEMPERATURE_HYSTERESIS_CONTROLLER_UPPER_LIMIT')
        thc_lower_limit = self.load_parameter('TEMPERATURE_HYSTERESIS_CONTROLLER_LOWER_LIMIT')

        if (thc_upper_limit is None) or (thc_lower_limit is None):
            # do nothing, leave default values
            pass        
        elif (thc_upper_limit < thc_lower_limit):
            # check for errors in parameters
            pass
        else:
            # set SW
            self.thc.set_upper_limit(thc_upper_limit)
            self.thc.set_lower_limit(thc_lower_limit)
            # set GUI
            self.update_spinbox_value.emit("maxHysteresisValue_temperature_spinBox", thc_upper_limit)
            self.update_spinbox_value.emit("minHysteresisValue_temperature_spinBox", thc_lower_limit)

        # HUMIDITY SPINBOX MIN/MAX
        hhc_upper_limit = self.load_parameter('HUMIDITY_HYSTERESIS_CONTROLLER_UPPER_LIMIT')
        hhc_lower_limit = self.load_parameter('HUMIDITY_HYSTERESIS_CONTROLLER_LOWER_LIMIT')

        if (hhc_upper_limit is None) or (hhc_lower_limit is None):
            # do nothing, leave default values
            pass        
        elif (hhc_upper_limit < hhc_lower_limit):
            # check for errors in parameters
            pass
        else:
            # set SW
            self.hhc.set_upper_limit(hhc_upper_limit)
            self.hhc.set_lower_limit(hhc_lower_limit)
            # set GUI
            self.update_spinbox_value.emit("maxHysteresisValue_humidity_spinBox", hhc_upper_limit)
            self.update_spinbox_value.emit("minHysteresisValue_humidity_spinBox", hhc_lower_limit)

        # HUMIDITY SETPOINT (dashboard): centro della banda di isteresi. Va emesso
        # sempre, anche quando i limiti non erano ancora stati salvati e restano
        # ai default, altrimenti la dashboard parte senza riferimento.
        self.publish_humidity_setpoint()

        # WATER LEVEL CONTROL SPINBOX MIN/MAX
        whc_upper_limit = self.load_parameter('WATER_LEVEL_CONTROL_HYSTERESIS_CONTROLLER_UPPER_LIMIT')
        whc_lower_limit = self.load_parameter('WATER_LEVEL_CONTROL_HYSTERESIS_CONTROLLER_LOWER_LIMIT')

        if (whc_upper_limit is None) or (whc_lower_limit is None):
            # do nothing, leave default values
            pass        
        elif (whc_upper_limit < whc_lower_limit):
            # check for errors in parameters
            pass
        else:
            # set SW
            self.whc.set_upper_limit(whc_upper_limit)
            self.whc.set_lower_limit(whc_lower_limit)
            # set GUI
            self.update_spinbox_value.emit("maxHysteresisValue_waterLevelControl_spinBox", whc_upper_limit)
            self.update_spinbox_value.emit("minHysteresisValue_waterLevelControl_spinBox", whc_lower_limit)

        # TEMPERATURE TIMINGS
        thc_time_on = self.load_parameter('TEMPERATURE_HYSTERESIS_CONTROLLER_TIME_ON')
        thc_time_off = self.load_parameter('TEMPERATURE_HYSTERESIS_CONTROLLER_TIME_OFF')
        if (thc_time_on is None) or (thc_time_off is None):
            pass
        else:
            # la visualizzazione si aggiornerà da sola periodicamente
            self.thc.set_time_on(thc_time_on) 
            self.thc.set_time_off(thc_time_off)             

        # HUMIDITY TIMINGS
        hhc_time_on = self.load_parameter('HUMIDITY_HYSTERESIS_CONTROLLER_TIME_ON')
        hhc_time_off = self.load_parameter('HUMIDITY_HYSTERESIS_CONTROLLER_TIME_OFF')
        if (hhc_time_on is None) or (hhc_time_off is None):
            pass
        else:
            # la visualizzazione si aggiornerà da sola periodicamente
            self.hhc.set_time_on(hhc_time_on) 
            self.hhc.set_time_off(hhc_time_off)     

        # EGG TURNS COUNTER
        turns_counter = self.load_parameter('TURNS_COUNTER')
        if turns_counter is not None:
            self.eggTurnerMotor.setTurnsCounter(turns_counter) # la visualizzazione si aggiornerà da sola periodicamente

        # ROTATION INTERVAL
        rotation_interval = self.load_parameter('ROTATION_INTERVAL')
        if rotation_interval is not None:
            self.eggTurnerMotor.setFunctionInterval(rotation_interval * 60) # Set the rotation interval
            self.update_spinbox_value.emit("rotation_interval_spinBox", rotation_interval) # aggiorno la visualizzazione
            
        # PID control
        temperature_PID_setPoint = self.load_parameter('TEMPERATURE_PID_SET_POINT')
        if temperature_PID_setPoint is not None:
            self.pid_temperature.set_reference_value(temperature_PID_setPoint)
            self.update_spinbox_value.emit("setPointTemperature_PID_spinBox", temperature_PID_setPoint) # aggiorno la visualizzazione
            
        # PID control
        temperature_PID_Kp_gain = self.load_parameter('TEMPERATURE_PID_KP_GAIN')
        if temperature_PID_Kp_gain is not None:
            self.pid_temperature.set_gain_Kp(temperature_PID_Kp_gain)
            self.update_spinbox_value.emit("Kp_spinBox", temperature_PID_Kp_gain) # aggiorno la visualizzazione
            
        # PID control
        temperature_PID_Ki_gain = self.load_parameter('TEMPERATURE_PID_KI_GAIN')
        if temperature_PID_Ki_gain is not None:
            self.pid_temperature.set_gain_Ki(temperature_PID_Ki_gain * (10 ** (-self.multiplier)))
            self.update_spinbox_value.emit("Ki_spinBox", temperature_PID_Ki_gain) # aggiorno la visualizzazione
            
        # PID control
        temperature_PID_Kd_gain = self.load_parameter('TEMPERATURE_PID_KD_GAIN')
        if temperature_PID_Kd_gain is not None:
            self.pid_temperature.set_gain_Kd(temperature_PID_Kd_gain)
            self.update_spinbox_value.emit("Kd_spinBox", temperature_PID_Kd_gain) # aggiorno la visualizzazione
            
        # data for incubation DAYS statistics
        incubation_start_date = self.load_parameter('INCUBATION_START_DATE')
        if incubation_start_date is not None:
            self.incubation_start_date = date.fromisoformat(incubation_start_date) 
            """
                print(f"self.incubation_start_date {self.incubation_start_date}  incubation_start_date {incubation_start_date}")
                self.incubation_start_date 2025-12-27  incubation_start_date 2025-12-27
            """
            self.update_date_edit.emit("startDate_dateTimeBox", date.fromisoformat(incubation_start_date))
            
        incubation_duration_days = self.load_parameter('INCUBATION_DURATION_DAYS') # type(incubation_duration_days) = int, viene caricato come intero
        if incubation_duration_days is not None:
            self.incubation_duration_days = incubation_duration_days
            self.update_int_spinbox_value.emit("days_duration_spinBox", incubation_duration_days)
            
        # Refresh rate della pagina web (solo display, nessun controllore da avvisare)
        web_refresh_interval_ms = self.load_parameter('WEB_REFRESH_INTERVAL_MS')
        if web_refresh_interval_ms is not None:
            self.update_spinbox_value.emit("webRefreshInterval_spinBox", web_refresh_interval_ms)

        # Velocità ventole ricircolo aria (0-100%): va rimandata subito ad Arduino,
        # altrimenti dopo un riavvio del programma le ventole restano ferme finché
        # l'utente non tocca di nuovo lo spinbox. Se il parametro non è ancora mai
        # stato salvato (prima esecuzione, parameters.json senza questa chiave), si
        # usa 100% come default e lo si invia comunque ad Arduino e alla dashboard,
        # invece di lasciare fan_speed_percent/gauge non inizializzati.
        fan_speed_percent = self.load_parameter('FAN_SPEED_PERCENT')
        if fan_speed_percent is None:
            fan_speed_percent = 100.0
        self.fan_speed_percent = fan_speed_percent
        self.queue_command("FAN01", round(fan_speed_percent / 100.0, 3))
        self.update_spinbox_value.emit("fanSpeed_spinBox", fan_speed_percent)

        pid_heating_mode_is_selected = self.load_parameter('PID_HEATING_MODE_IS_SELECTED')
        if pid_heating_mode_is_selected is not None:
            # persistenza nella modalità di riscaldamento scelta
            self.pid_temperature_is_activated = pid_heating_mode_is_selected
            self.update_radio_button_exclusive.emit('PIDActive_radioBtn', self.pid_temperature_is_activated)

        # Heater/Humidifier/Valve: forceON e forceOFF sono False di default in HysteresisController,
        # cioè il controllore parte già in AUTO. Sincronizzo il radio button della pagina web con
        # questo stato reale, altrimenti all'apertura nessun radio button risulta selezionato.
        self.update_radio_button_exclusive.emit('heaterAUTO_radioBtn', True)
        self.update_radio_button_exclusive.emit('humidifierAUTO_radioBtn', True)
        self.update_radio_button_exclusive.emit('evalveAUTO_radioBtn', True)
            
            
    def _load_all_parameters(self):
        # Se il caricamento fallisce mentre il file esiste, NON si deve
        # sovrascrivere il file con un dizionario vuoto/parziale.
        self.parameters_load_failed = False
        if os.path.exists(self.parameters_file_path):
            try:
                with open_with_retry(self.parameters_file_path, "r") as f:
                    self.parameters = json.load(f)
            except Exception as e:
                print(f"Errore nel caricamento dei parametri: {e}")
                self.parameters = {}
                self._load_from_backup()
                if not self.parameters:
                    self.parameters_load_failed = True
                    print("ATTENZIONE: parametri non caricati, il salvataggio su file è disabilitato "
                          "per non perdere parameters.json. Usare i valori di default e riavviare.")
        else:
            self.parameters = {}

    def _save_all_parameters(self):
        """Salva tutti i parametri nel file, con backup automatico."""
        if getattr(self, 'parameters_load_failed', False):
            print("Salvataggio parametri saltato: il file non era leggibile all'avvio.")
            return
        try:
            # Se il file originale esiste, crea una copia di backup
            '''
            if os.path.exists(self.parameters_file_path):
                backup_path = self.parameters_file_path + ".bak"
                shutil.copy2(self.parameters_file_path, backup_path)
            '''

            # Ora salva il nuovo contenuto
            with open_with_retry(self.parameters_file_path, "w") as f:
                json.dump(self.parameters, f, indent=4)
        except Exception as e:
            print(f"Errore nel salvataggio dei parametri: {e}")

    def load_parameter(self, key):
        """Restituisce il valore del parametro, o None se non esiste."""
        return self.parameters.get(key, None)

    def save_parameter(self, key, value):
        """Salva o aggiorna un parametro e lo scrive su file."""
        self.parameters[key] = value
        self.main_software_thread_log_message('INFO', f"Saving parameter {key}: {value}", suppress_terminal_print = True)
        self._save_all_parameters()

    def _load_from_backup(self):
        backup_path = self.parameters_file_path + ".bak"
        if os.path.exists(backup_path):
            try:
                with open_with_retry(backup_path, "r") as f:
                    self.parameters = json.load(f)
                print("Parametri caricati dal backup.")
            except Exception as e:
                print(f"Errore nel caricamento del backup: {e}")

    def backup_parameters_file(self):
        """Crea una copia di backup di parameters.json con data e ora nel nome, estensione .bak."""
        if not os.path.exists(self.parameters_file_path):
            print("Nessun file di parametri da salvare.")
            return

        # Estrai nome base e cartella
        dir_path = os.path.dirname(self.parameters_file_path)
        base_name = os.path.splitext(os.path.basename(self.parameters_file_path))[0]

        # Costruisci nome con timestamp
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup_filename = f"{base_name}_{ts}.json.bak"
        backup_path = os.path.join(dir_path, backup_filename)

        try:
            shutil.copy2(self.parameters_file_path, backup_path)
            print(f"Backup creato: {backup_filename}")
        except Exception as e:
            print(f"Errore durante il backup: {e}")

    # === PID Controller with Automatic Timing === #
    ''' CHAT GPT
    class PIDController:
        def __init__(self, kp=0.0, ki=0.0, kd=0.0,
                    reference=0.0,
                    output_min=float("-inf"),
                    output_max=float("inf")):
            
            # Current value - valore che viene dal feedback
            self.current_value = 0.0

            # Gains
            self.kp = kp
            self.ki = ki
            self.kd = kd

            # Setpoint
            self.reference = reference

            # Output limits
            self.output_min = output_min
            self.output_max = output_max

            # Internal state
            self.integral = 0.0
            self.prev_error = None
            self.output = 0.0

            # Time tracking
            self.last_time = None

            self.forceON = False
            self.forceOFF = False

        # --------------------------
        # Configuration methods
        # --------------------------
        def set_gains(self, kp, ki, kd):
            self.kp = kp
            self.ki = ki
            self.kd = kd
            
        def set_gain_Kp(self, value):
            self.kp = value
            
        def set_gain_Ki(self, value):
            self.ki = value
            
        def set_gain_Kd(self, value):
            self.kd = value

        def set_reference_value(self, ref_value):
            self.reference = ref_value

        def get_reference_value(self):
            return self.reference

        def set_output_limits(self, min_value, max_value):
            self.output_min = min_value
            self.output_max = max_value

        def set_control_mode(self, control_mode):
            if control_mode == "forceON":
                self.forceON = True
                self.forceOFF = False
            if control_mode == "forceOFF":
                self.forceON = False
                self.forceOFF = True
            if control_mode == "AUTO":
                self.forceON = False
                self.forceOFF = False

        # --------------------------
        # Update cycle
        # --------------------------
        def update(self, current_value_loc, dt_threshold):
            """
            Compute PID output with automatic dt measurement,
            clamping, and anti-windup.

            dt_threshold = SECONDS → PID updates only if at least 1s have passed since last update.
            """

            now = time.time()

            # ---- Calculate dt automatically ----
            if self.last_time is None:
                self.last_time = now
                dt = 0.0  # first call; no action possible
            else:
                dt = now - self.last_time

            # Only proceed if enough time has passed
            if dt < dt_threshold:
                # Not enough time elapsed; return previous output
                return False

            self.last_time = now

            if self.forceON: 
                self.output = self.output_max  
                return True            
            elif self.forceOFF:  
                self.output = self.output_min      
                return True         
            else: # AUTO  
                pass 

            self.current_value = current_value_loc
            error = self.reference - self.current_value

            # ----- Derivative term -----
            if self.prev_error is None or dt == 0:
                derivative = 0.0
            else:
                derivative = (error - self.prev_error) / dt

            # ----- Integral term (pre-windup) -----
            if dt > 0:
                self.integral += error * dt

            # Raw output before clamping
            raw_output = (
                self.kp * error +
                self.ki * self.integral +
                self.kd * derivative
            )

            # ----- Output clamping -----
            clamped_output = max(self.output_min, min(raw_output, self.output_max))

            # ----- Anti-windup -----
            if raw_output != clamped_output and dt > 0:
                # Undo integrator accumulation
                self.integral -= error * dt

            self.output = clamped_output
            self.prev_error = error

            return True

        def get_current_value(self):
            return self.current_value    
        
        def get_output(self):
            return self.output 

        def get_output_for_arduino(self):
            """
            Adding a dedicated method that converts the PID output to an Arduino-friendly 8-bit PWM value (0–255). 
            This is just a linear mapping from your existing output_min/output_max range to [0, 255].

            Convert the PID output to an integer 0–255 for Arduino PWM.
            Assumes self.output_min and self.output_max define the PID output range.
            """
            # Clip output to expected range just in case
            output_clamped = max(self.output_min, min(self.output, self.output_max))

            # Map output to 0–255
            pwm_value = int((output_clamped - self.output_min) / 
                            (self.output_max - self.output_min) * 255)

            # Clip again to ensure it stays within 0–255
            pwm_value = max(0, min(pwm_value, 255))

            return pwm_value    

    '''
    # COPILOT PID CONTROLLER #
    
    class PIDController:
        def __init__(self, kp=0.0, ki=0.0, kd=0.0,
                    reference=0.0,
                    output_min=0.0,         # [0..1] per PWM
                    output_max=1.0):        # [0..1] per PWM
            
            # Current value - valore che viene dal feedback
            self.current_value = 0.0

            # Gains (Ki espresso in [1/s])
            self.kp = kp
            self.ki = ki
            self.kd = kd

            # Setpoint
            self.reference = reference

            # Output limits (normalizzati: 0..1 -> PWM 0..255)
            self.output_min = output_min
            self.output_max = output_max

            # Internal state
            self.integral = 0.0
            self.prev_error = None
            self.prev_output = 0.0
            self.output = 0.0

            # Time tracking
            self.last_time = None

            # Modalità
            self.forceON = False
            self.forceOFF = False

            # -----------------------------
            # Feedforward termico
            # -----------------------------
            self.ff_enabled = False
            self.ff = 0.0
            self.K_process = 1.0      # °C per unità di comando (u in [0,1])
            self.ambient_value = 0.0  # Tamb (°C)

            # Aggiungere un trim del feedforward (bias adattivo, molto lento)            
            self.u_trim = 0.0         # bias adattivo del feedforward
            self.T_trim = 3600.0      # [s] costante di tempo del trim (es. 1 h)
            self.trim_min = -0.2      # limiti di sicurezza sul bias
            self.trim_max =  0.2

            # Filtrare la misura (EMA 10–20 s) e usare deadband coerente
            # exponential moving average filter - l'errore letto diventa continuo e non più a gradini di 0.1°C
            self.meas_alpha = 0.2    # filtro EMA (costante ~10–20 s con Ts=2 s)
            self._y_filt    = None

            self.int_deadband = 0.08 # °C, coerente con misura filtrata
            self.int_db_gain  = 0.20 # integra al 20% dentro la deadband



        # --------------------------
        # Configuration methods
        # --------------------------
        def set_gains(self, kp, ki, kd):
            self.kp = kp
            self.ki = ki
            self.kd = kd

        def set_gain_Kp(self, value):
            self.kp = value

        def set_gain_Ki(self, value):
            self.ki = value

        def set_gain_Kd(self, value):
            self.kd = value

        def set_reference_value(self, ref_value):
            self.reference = ref_value

        def get_reference_value(self):
            return self.reference

        def set_output_limits(self, min_value, max_value):
            self.output_min = min_value
            self.output_max = max_value

        def set_control_mode(self, control_mode):
            if control_mode == "forceON":
                self.forceON = True
                self.forceOFF = False
            elif control_mode == "forceOFF":
                self.forceON = False
                self.forceOFF = True
            elif control_mode == "AUTO":
                # Bumpless transfer: ricalibra l'integrale per evitare salti
                # Iterm = (output_desiderato - ff - Kp*e)/Ki
                # se Ki==0, non toccare l'integrale
                self.forceON = False
                self.forceOFF = False
                if self.ki > 0.0 and self.prev_error is not None:
                    self.ff = self._compute_feedforward()
                    desired_u = self.output  # resta dove sei
                    self.integral = max(self.output_min, min(self.output_max,
                                        desired_u)) - self.ff - self.kp * self.prev_error
                    # L'integrale è in unità "u"; l'aggiornamento in update terrà conto di dt

        def set_feedforward_params(self, K_process, ambient_value, enable=True):
            """
            Imposta i parametri del feedforward termico.
            K_process: °C per unità (u in [0..1])
            ambient_value: temperatura ambiente (°C)
            enable: abilita/disabilita feedforward
            """
            self.K_process = float(K_process)
            self.ambient_value = float(ambient_value)
            self.ff_enabled = bool(enable)

        def update_ambient(self, ambient_value):
            """Aggiorna la temperatura ambiente in tempo reale (opzionale)."""
            self.ambient_value = float(ambient_value)

        # --------------------------
        # Update cycle
        # --------------------------
        def update(self, current_value_loc, dt_threshold):
            """
            Compute PID+FF output con misura automatica di dt,
            clamping e anti-windup.
            
            dt_threshold = secondi → il PID aggiorna solo se sono passati almeno dt_threshold secondi.
            """

            now = time.time()

            # ---- Calculate dt automatically ----
            if self.last_time is None:
                self.last_time = now
                dt = 0.0  # first call; no action possible
            else:
                dt = now - self.last_time

            # Only proceed if enough time has passed
            if dt < dt_threshold:
                # Non abbastanza tempo: ritorna output precedente
                return False

            self.last_time = now

            # Modalità manuali
            if self.forceON:
                self.output = self.output_max
                self.prev_output = self.output
                return True
            elif self.forceOFF:
                self.output = self.output_min
                self.prev_output = self.output
                return True
            # altrimenti AUTO

            
            # -----------------------------
            # Lettura misura con EMA (opzionale)
            # -----------------------------
            meas_alpha = getattr(self, 'meas_alpha', None)  # None → disabilitato
            y_meas = float(current_value_loc)
            if meas_alpha is not None:
                # stato filtro
                if getattr(self, '_y_filt', None) is None:
                    self._y_filt = y_meas
                else:
                    self._y_filt = meas_alpha * y_meas + (1.0 - meas_alpha) * self._y_filt
                self.current_value = self._y_filt
            else:
                self.current_value = y_meas

            # -----------------------------
            # Errori e derivative
            # -----------------------------
            beta = getattr(self, 'beta', 1.0)  # setpoint weighting sul P
            error = self.reference - self.current_value
            e_P   = beta * self.reference - self.current_value

            prev_err = getattr(self, 'prev_error', None)
            if dt <= 0.0 or prev_err is None:
                derivative = 0.0
            else:
                derivative = (error - prev_err) / dt

            # -----------------------------
            # Feedforward = nominale + trim lento
            # -----------------------------
            self.ff = self._compute_feedforward()  # nominale
            u_trim   = getattr(self, 'u_trim', 0.0)
            T_trim   = getattr(self, 'T_trim', 0.0)
            trim_min = getattr(self, 'trim_min', -0.2)
            trim_max = getattr(self, 'trim_max',  +0.2)

            ff_total = self.ff + u_trim

            # -----------------------------
            # Uscita non clippata: FF + P + I + D
            # -----------------------------
            raw_output_noI = ff_total + self.kp * e_P + self.kd * derivative
            raw_output     = raw_output_noI + self.ki * self.integral

            # Clamping
            clamped_output = max(self.output_min, min(raw_output, self.output_max))
            saturated      = (clamped_output != raw_output)

            # -----------------------------
            # Soft-deadband sull'integrale
            # -----------------------------
            int_deadband = getattr(self, 'int_deadband', 0.10)  # °C
            int_db_gain  = getattr(self, 'int_db_gain',  0.20)  # fattore 0..1 dentro la deadband

            if int_deadband is None or int_deadband <= 0.0:
                w_int = 1.0
            else:
                abs_e = abs(error)
                if abs_e >= int_deadband:
                    w_int = 1.0
                else:
                    # rampa lineare: a 0 → 0, a deadband → int_db_gain
                    # (micro-integrazione per memorizzare il bias anche con errore quantizzato)
                    eps  = 1e-9
                    w_int = int_db_gain * (abs_e / max(int_deadband, eps))

            # -----------------------------
            # Anti-windup + integrazione pesata
            # -----------------------------
            if dt > 0.0:
                if not saturated:
                    self.integral += w_int * error * dt
                else:
                    # integra solo se l'errore tende a far rientrare dalla saturazione
                    if (raw_output > self.output_max and error < 0) or \
                    (raw_output < self.output_min and error > 0):
                        self.integral += w_int * error * dt
                    # altrimenti non integra

            # -----------------------------
            # Adattamento TRIM (molto lento)
            # -----------------------------
            # Avvicina (FF+trim) all'uscita applicata indipendentemente dall'errore (utile anche quando errore=0)
            if dt > 0.0 and T_trim and T_trim > 0.0:
                #u_trim += (clamped_output - raw_output_noI) * (dt / T_trim)
                u_trim += (raw_output_noI - clamped_output) * (dt / T_trim)
                # limiti di sicurezza
                if u_trim < trim_min: u_trim = trim_min
                if u_trim > trim_max: u_trim = trim_max
                self.u_trim = u_trim  # salva back

            # -----------------------------
            # Ricalcolo finale output con integral aggiornato
            # -----------------------------
            ff_total = self.ff + getattr(self, 'u_trim', 0.0)
            raw_output_noI = ff_total + self.kp * e_P + self.kd * derivative
            raw_output     = raw_output_noI + self.ki * self.integral

            self.output = max(self.output_min, min(raw_output, self.output_max))
            self.prev_output = self.output
            self.prev_error  = error  # aggiorno qui, dopo avere usato prev_err

            # DEBUGGING
            print(
                    f"Kp {self.kp}"  
                    f" Ki {round(self.ki, 4)}"
                    f" Integral action: {round(self.ki * self.integral, 3)}"  
                    f" Output: {self.output}"
                    f" ff: {round(self.ff, 4)}" 
                    f" U_trim: {self.u_trim}"   
                    f" ff_tot: {round(ff_total, 4)}" 
                    f" e: {e_P}"                  
                    )

            return True


        # --------------------------
        # Helpers
        # --------------------------
        def _compute_feedforward(self):
            if not self.ff_enabled or self.K_process <= 0.0:
                return 0.0
            # u_ff = (Tsp - Tamb) / K_process  → clamp [0..1]
            u_ff = (self.reference - self.ambient_value) / self.K_process
            return max(self.output_min, min(u_ff, self.output_max))

        def get_current_value(self):
            return round(self.current_value, 1)   

        def get_output(self):
            return self.output

        '''
        copilot
        def get_output_for_arduino(self):
            """
            Converte l'uscita normalizzata [0..1] in PWM 0–255 per Arduino.
            Usa i limiti output_min/output_max per scalare.
            """
            output_clamped = max(self.output_min, min(self.output, self.output_max))
            pwm_value = int(round((output_clamped - self.output_min) /
                                (self.output_max - self.output_min) * 255.0))
            pwm_value = max(0, min(pwm_value, 255))
            
            return pwm_value
        '''
        def get_output_for_arduino(self):
            output_clamped = max(self.output_min, min(self.output, self.output_max))

            pwm_float = (
                (output_clamped - self.output_min) /
                (self.output_max - self.output_min) * 255.0
            )

            # filtro
            self._pwm_filt = 0.8 * getattr(self, "_pwm_filt", pwm_float) + 0.2 * pwm_float

            return max(0, min(int(self._pwm_filt), 255))
        
        def get_normalized_output(self):
            # 2 cifre decimali
            value = max(self.output_min, min(self.output, self.output_max))
            return math.trunc(value * 100) / 100


    class HysteresisController:
        def __init__(self, lower_limit, upper_limit):            
            self.lower_limit = lower_limit
            self.upper_limit = upper_limit
            self._min_value = float('inf')  # Track the minimum value observed
            self._max_value = float('-inf') # Track the maximum value observed
            self._mean_value = None         # Mean value of the monitored inputs
            self._output_control = False    # False = OFF, True = ON
            
            # Statistics tracking
            self.on_count = 0
            self.off_count = 0
            
            self.reset_timer_to_do = True
            self.measuring_time_interval_sec = 1 # ogni n secondi aggiorno i conteggi di tempo on/off dell'attuatore
            self.last_measuring_time = time.time() # si ricorda appena ho preso il campione precedente
            self.effective_time_difference = 0.0
            # questi sono i contatori di timer
            self.time_on = 0.0
            self.time_off = 0.0
            
            self._last_switch_time = time.time()
            
            self.forceON = False
            self.forceOFF = False

        def set_time_on(self, time_on):
            self.time_on = time_on

        def set_time_off(self, time_off):
            self.time_off = time_off

        # Method to set the lower limit
        def set_lower_limit(self, lower_limit):
            self.lower_limit = lower_limit
            if self.lower_limit == self.upper_limit:
                self._output_control = False  # Safety enforcement
        
        # Method to get the lower limit
        def get_lower_limit(self):
            return self.lower_limit
        
        # Method to set the upper limit
        def set_upper_limit(self, upper_limit):
            self.upper_limit = upper_limit
            if self.lower_limit == self.upper_limit:
                self._output_control = False  # Safety enforcement
                
        def set_control_mode(self, control_mode):
            if control_mode == "forceON":
                self.forceON = True
                self.forceOFF = False
            if control_mode == "forceOFF":
                self.forceON = False
                self.forceOFF = True
            if control_mode == "AUTO":
                self.forceON = False
                self.forceOFF = False
        
        # Method to get the upper limit
        def get_upper_limit(self):
            return self.upper_limit

        # Update method to process input values and apply hysteresis logic
        def update(self, values):
            # reset timers all'avvio
            if self.reset_timer_to_do:
                self.last_measuring_time = time.time()
                self.reset_timer_to_do = False
                
            '''
            # Safety check: If limits are equal, force OFF state
            if self.lower_limit == self.upper_limit:
                self._output_control = False
                return
            '''
        
            # Ensure values is a list for consistency
            if not isinstance(values, list):
                values = [values]
            
            
            new_output = self._output_control
            
            if not values:
                new_output = False # FOR SAFETY!! no values in input, means no good temperatures are passed, then OFF the actuator
            else:            
                # Calculate the mean of the values
                self._mean_value = round(sum(values) / len(values), 1)
                            
                # Update the max and min values reached
                for value in values:
                    self._max_value = max(self._max_value, value)
                    self._min_value = min(self._min_value, value)                
                
                # Check if output state changes
                if self.lower_limit == self.upper_limit:
                    new_output = False
                elif self.forceON: 
                    new_output = True               
                elif self.forceOFF:  
                    new_output = False              
                else: # AUTO                
                    if self._mean_value >= self.upper_limit:
                        new_output = False  # Turn OFF if mean is above upper limit
                    elif self._mean_value <= self.lower_limit:
                        new_output = True   # Turn ON if mean is below lower limit
                
            # update time tracking (continuous)
            elapsed_time = time.time() - self.last_measuring_time
            if elapsed_time >= self.measuring_time_interval_sec:
                if self._output_control:
                    self.time_on += elapsed_time
                else:
                    self.time_off += elapsed_time
                self.last_measuring_time = time.time()
                
            # If state changed, update count transitions
            if new_output != self._output_control:
                elapsed_time = time.time() - self.last_measuring_time
                if self._output_control:
                    self.time_on += elapsed_time
                    self.off_count += 1
                else:
                    self.time_off += elapsed_time
                    self.on_count += 1  
                self.last_measuring_time = time.time()                
                
            self._output_control = new_output
            
                
        
        # Method to get the output control (True or False)
        def get_output_control(self):
            return self._output_control
        
        # Method to get the maximum value reached
        def get_max_value(self):
            return self._max_value
        
        # Method to get the minimum value reached
        def get_min_value(self):
            return self._min_value

        # Method to get the mean value of the last monitored inputs
        def get_mean_value(self):
            return self._mean_value

        # Method to reset min/max values
        def reset_max_value(self):
            self._max_value = float('-inf')
        
        def reset_min_value(self):
            self._min_value = float('inf')

        def reset_all_values(self):
            self._min_value = float('inf')
            self._max_value = float('-inf')

        # Method to get the number of ON/OFF transitions
        def get_on_count(self):
            return self.on_count

        def get_off_count(self):
            return self.off_count

        # Method to get the total time spent ON/OFF
        def get_time_on(self):
            return round(self.time_on, 0)

        def get_time_off(self):
            return round(self.time_off, 0)

        # Method to reset statistics
        def reset_time_statistics(self):
            self.on_count = 0
            self.off_count = 0
            self.time_on = 0.0
            self.time_off = 0.0
            self._last_switch_time = time.time()
        
        def reset_absolute_temperatures_statistics(self):
            self._max_value = float('-inf')
            self._min_value = float('inf')
            
    class StepperMotor:
        def __init__(self, name="Stepper1"):
            self.name = name
            self.running = False
            
            """
                self.main_state:
                1 = INITIALIZATION
                10 MANUAL   _MODE
                30 AUTOMATIC_MODE
            
            """
            self.main_state = "INITIALIZATION"
            
            self.manual_state = "WAITING_FOR_COMMAND"
            
            '''
                Vogio gestire tutto con una variabile...anziché avere direzione e movimento, ne ho una sola
                Il tribolo è lo stop. Not moving, ma in che direzione? allora faccio due valori della variabile, che sono loro che si ricordano
                da dove mi stavo fermando
                stopped_from_CCW_rotation_direction
                stopped_from_CW_rotation_direction
                
                self.rotation_state 
                not_defined
                CCW_reached
                CW_reached
                CCW_rotation_direction
                CW_rotation_direction
                laying_horizontal_position
                
            '''
            self.rotation_state = "not_defined"
            
            # BUTTON: these are two internal buttons I'll use for manual movements CW and CCW            
            self.buttons = {
                "move_CW_motor_btn":  False, # = not pressed
                "move_CCW_motor_btn": False, # = not pressed
            }
            
            
            self.last_execution_time = time.time()
            self.auto_function_interval_sec = 3600  # Default to 1 hour
            self.last_ack_time = None
            self.alarm_triggered = False
            self.turnsCounter = 0
            
            self.stop_command = False
            
            self.new_command = None
            self.update_motor_data = False
            self.acknowledge_from_external = None            
            
            self.force_change_rotation_flag = False
            
            self.rotation_in_progress_timeout_sec = 120 # 2min timeout
            
            # TIMING
            self.last_motor_data_update_sec = time.time()
            self.motor_data_update_interval_sec = 15 # indica ogni quanti secondi viene fatto l'update dei dati relativi al motore

            # Variabili che uso per il salvataggio dei dati: mi servono per: 
            # 1) fare la foto allo stato stabile di rotazione che raggiungo
            # 2) fare la foto all'istante (datetime) in cui questa cosa succede

            self.last_time_stable_position_is_reached = None
            self.last_stable_position = None
            
        def pressButton(self, button_name: str):
            if button_name not in self.buttons:
                raise ValueError(f"Button '{button_name}' not valid. Use: {list(self.buttons.keys())}")

            self.buttons[button_name] = not self.buttons[button_name]
            state = self.buttons[button_name]
            print(f"{self.name}: {button_name} button -> {'PRESSED' if state else 'RELEASED'}")
            
            if state: # Premuto
                if button_name == "move_CW_motor_btn":
                    self.moveCWContinuous()
                elif button_name == "move_CCW_motor_btn":
                    self.moveCCWContinuous()
            else: #Rilasciato
                self.stop()
            

        def setTurnsCounter(self, value):
            self.turnsCounter = value

        def moveCWContinuous(self):
            self.main_state = "MANUAL_MODE"
            self.manual_state = "MOVING_CW_CONTINUOUSLY"
            print(f"{self.name}: Moving cw continuously.")

        def moveCCWContinuous(self):
            self.main_state = "MANUAL_MODE"
            self.manual_state = "MOVING_CCW_CONTINUOUSLY"
            print(f"{self.name}: Moving ccw continuously.")

        def stop(self):
            self.stop_command = True
            print(f"{self.name}: Stopping motor.")
        
        def setFunction1(self): # ?? non la usa nessuno
            self.main_state = "AUTOMATIC_MODE"
            self.last_ack_time = time.time()
            self.alarm_triggered = False
            print(f"{self.name}: Automatic function 1 enabled.")
        
        def stopFunction1(self): # ?? non la usa nessuno
            self.main_state = "STOPPED"
            print(f"{self.name}: Stopping automatic function 1.")
        
        def setFunctionInterval(self, interval):
            self.auto_function_interval_sec = interval
            print(f"{self.name}: Automatic function interval set to {interval} seconds.")
        
        def acknowledgeFromExternal(self, ack):
            self.last_ack_time = time.time()
            self.acknowledge_from_external = ack  
            print(f"Acknowledge received from external:{ack}")          
            
        def resetNewCommand(self):
            self.new_command = None
            
        def resetUpdateMotorData(self):
            self.update_motor_data = None
        
        def getRotationDirection(self):
            return self.rotation_direction
        
        def getTimeSinceLastRotation(self):
            time_result = round(time.time() - self.last_execution_time, 0) # in seconds
            return time_result
        
        def getTimeUntilNextRotation(self):
            time_result = round(max(0, self.auto_function_interval_sec - (time.time() - self.last_execution_time)), 0) # in seconds
            #print(f"Time until next rotation:{time_result} seconds")  
            return time_result
        
        def getTurnsCounter(self):
            return self.turnsCounter
        
        def getUpdateMotorData(self):
            return self.update_motor_data
        
        def getNewCommand(self):
            return self.new_command
        
        def forceEggsRotation(self):
            self.force_change_rotation_flag = True
            self.main_state = "AUTOMATIC_MODE" # go back in automatic, if you were in manual
        
        def update(self):
            current_time = time.time()
            previous_rotation_state = self.rotation_state
            
            if self.main_state == "INITIALIZATION":
                if self.acknowledge_from_external is not None:                    
                    if self.acknowledge_from_external == "IND_CCW": 
                        self.rotation_state = "CCW_reached"
                        print("Homing done. Reached the Counter Clock Wise limit switch")
                        self.acknowledge_from_external = None # reset
                        
                    if self.acknowledge_from_external == "IND_CW":
                        self.rotation_state = "CW_reached"
                        print("Homing done. Reached the Clock Wise limit switch")                        
                        self.acknowledge_from_external = None # reset
                        
                    self.last_execution_time = current_time # salvo il tempo, perché così la prossima FULL TURN avviene contando il tempo da quando ho finito l'homing
                    self.main_state = "AUTOMATIC_MODE"
                else:
                    pass
                
            elif self.main_state == "MANUAL_MODE":                    
                    if self.manual_state == "WAITING_FOR_COMMAND": #waiting for command
                        pass
                        
                    if self.manual_state == "MOVING_CW_CONTINUOUSLY": # CW continuous moving
                        self.rotation_state = "CW_rotation_direction"
                        self.new_command = "manual_" + self.rotation_state
                        self.manual_state = "ROTATION_IN_PROGRESS" 
                        
                    if self.manual_state == "MOVING_CCW_CONTINUOUSLY": # CCW continuous moving
                        self.rotation_state = "CCW_rotation_direction"
                        self.new_command = "manual_" + self.rotation_state
                        self.manual_state = "ROTATION_IN_PROGRESS" 
                        
                    if self.manual_state == "ROTATION_IN_PROGRESS": # rotation in progress
                        # IF ack from limit switch --> comunica che è arrivato ack, ma di fatto si ferma da solo per Arduino
                        if self.acknowledge_from_external is not None:         
                            if self.acknowledge_from_external == "IND_CCW" and self.rotation_state == "CCW_rotation_direction":
                                self.rotation_state = "CCW_reached"
                                print("Reached the IND_CCW limit switch")
                                self.acknowledge_from_external = None # reset
                                self.manual_state = "WAITING_FOR_COMMAND"
                                self.buttons["move_CCW_motor_btn"] = False # Devo azzerare lo stato, altrimenti si imbanana (rimane 'premuto' nonostante sia arrivato da solo al finecorsa)
                                
                            if self.acknowledge_from_external == "IND_CW" and self.rotation_state == "CW_rotation_direction":
                                self.rotation_state = "CW_reached"
                                print("Reached the IND_CW limit switch")
                                self.acknowledge_from_external = None # reset
                                self.manual_state = "WAITING_FOR_COMMAND"
                                self.buttons["move_CW_motor_btn"] = False
                        
                    if self.manual_state == "STOPPED":
                        self.new_command = "stop"
                        
                        if self.rotation_state == "CCW_rotation_direction":
                            self.rotation_state = "stopped_from_CCW_rotation_direction"
                            
                        if self.rotation_state == "CW_rotation_direction":
                            self.rotation_state = "stopped_from_CW_rotation_direction"
                            
                        self.manual_state = "WAITING_FOR_COMMAND"
                        
                    # IF stop command, then stop
                    if self.stop_command:
                        self.manual_state = "STOPPED"
                        self.stop_command = False
                        
            if ((time.time() - self.last_motor_data_update_sec) >= self.motor_data_update_interval_sec # update periodico
                or
                (self.new_command is not None and "manual_" in self.new_command) # forzatura dell'update se non è scaduto il tempo ma se è scattato un comando di RUN manuale
                or
                (self.new_command is not None and "stop" in self.new_command) # forzatura dell'update se non è scaduto il tempo ma se è scattato un comando di stop
                or
                (previous_rotation_state == "CCW_rotation_direction" and self.rotation_state == "CCW_reached") # forzatura update della visu se non è scaduto il tempo ma ho raggiunto il finecorsa
                or
                (previous_rotation_state == "CW_rotation_direction" and self.rotation_state == "CW_reached")
                ):
                self.update_motor_data = True
                self.last_motor_data_update_sec = time.time()
            
            elif self.main_state == "AUTOMATIC_MODE":
                if (current_time - self.last_execution_time >= self.auto_function_interval_sec or self.force_change_rotation_flag):
                    
                    if self.rotation_state == "CCW_reached" or self.rotation_state == "CCW_rotation_direction" or self.rotation_state == "stopped_from_CCW_rotation_direction":
                        self.rotation_state = "CW_rotation_direction"
                        
                    elif self.rotation_state == "CW_reached" or self.rotation_state == "CW_rotation_direction" or self.rotation_state == "stopped_from_CW_rotation_direction":
                        self.rotation_state = "CCW_rotation_direction"
                        
                    print(f"{self.name}: Changing rotation direction to {self.rotation_state}.")                    
                    self.last_execution_time = current_time
                    self.turnsCounter += 1
                    self.new_command = "automatic_" + self.rotation_state
                    print(f"{'Activating' if (self.rotation_state == 'CW_rotation_direction' or self.rotation_state == 'CCW_rotation_direction') else 'Deactivating'} diagnostics")                    
                    
                if self.force_change_rotation_flag:
                    self.force_change_rotation_flag = False # resetting
                    
                    
                if (self.rotation_state == "CCW_rotation_direction" or self.rotation_state == "CW_rotation_direction"):
                    # diagnostic: attesa del feedback
                    if (time.time() - self.last_execution_time) >= self.rotation_in_progress_timeout_sec:
                        # manda comando di STOP
                        #self.new_command = "stop"
                        pass
                        
                    if self.acknowledge_from_external is not None:         
                        if self.acknowledge_from_external == "IND_CCW" and self.rotation_state == "CCW_rotation_direction":
                            self.rotation_state = "CCW_reached"
                            self.last_stable_position = self.rotation_state
                            self.last_time_stable_position_is_reached = datetime.now()
                            print(f"{'Activating' if (self.rotation_state == 'CW_rotation_direction' or self.rotation_state == 'CCW_rotation_direction') else 'Deactivating'} diagnostics")
                            print("Reached the IND_CCW limit switch")
                            self.acknowledge_from_external = None # reset
                            
                        if self.acknowledge_from_external == "IND_CW" and self.rotation_state == "CW_rotation_direction":
                            self.rotation_state = "CW_reached"
                            self.last_stable_position = self.rotation_state
                            self.last_time_stable_position_is_reached = datetime.now()
                            print(f"{'Activating' if (self.rotation_state == 'CW_rotation_direction' or self.rotation_state == 'CCW_rotation_direction') else 'Deactivating'} diagnostics")
                            print("Reached the IND_CW limit switch")
                            self.acknowledge_from_external = None # reset
                            
                else:
                    pass
					
		# ogni tot diciamo che è passato il tempo per fare un update dei dati del motore: il programma, da fuori, riceve il segnale e prende i dati
                #print(time.time() - self.last_motor_data_update_sec)
            if ((time.time() - self.last_motor_data_update_sec) >= self.motor_data_update_interval_sec # update periodico
                or 
                self.force_change_rotation_flag # forzatura dell'update quando forzo un giro a mano
                or
                (self.new_command is not None and "automatic_" in self.new_command) # forzatura dell'update se non è scaduto il tempo ma se è scattato il comando di turn
                or
                (previous_rotation_state == "CCW_rotation_direction" and self.rotation_state == "CCW_reached") # forzatura update della visu se non è scaduto il tempo ma ho raggiunto il finecorsa
                or
                (previous_rotation_state == "CW_rotation_direction" and self.rotation_state == "CW_reached")
                ):
                self.update_motor_data = True
                self.last_motor_data_update_sec = time.time()
                
            #print(f"{self.main_state} {self.manual_state} {self.rotation_state} {self.new_command}")
                            
                        
                    
                
                
            '''
            if self.last_ack_time and (current_time - self.last_ack_time > 180):  # 3 minutes timeout
                if not self.alarm_triggered:
                    self.stop()
                    print(f"{self.name}: ERROR! No acknowledgment received for 2 minutes. Stopping motor and raising alarm!")
                    self.alarm_triggered = True
            '''
            
            if self.acknowledge_from_external is not None:   
                '''
                    I feedback degli induttori sono msi asincroni dal process serial data dentro qusta variabile. Se arriva per qualche motivo mentre
                    sono in uno stato dove non me lo aspetto, allora non viene resettato, rimane in variabile e dopo apena entro in modalità automatica
                    la variabile l'ha ancora in pancia e quindi dà una falsa lettura. Allora, alla fine del ciclo, se non l'ho usato, lo resetto.
                '''                  
                self.acknowledge_from_external = None # reset 

class WebBridge(QtCore.QObject):
    """
    Replaces MainWindow.  Bridges MainSoftwareThread (QThread/pyqtSignal) with the
    web front-end (Flask-SocketIO).  Lives in the Qt main thread so all signals from
    MainSoftwareThread are delivered here via Qt's queued connection.
    """

    # Signals sent TO MainSoftwareThread (same interface as MainWindow)
    button_clicked               = QtCore.pyqtSignal(str)
    float_spinBox_value_changed  = QtCore.pyqtSignal(str, float)
    initialization_step          = QtCore.pyqtSignal(str, float)
    initialization_done          = QtCore.pyqtSignal(str, bool)
    radio_button_toggled         = QtCore.pyqtSignal(str, bool)
    date_changed                 = QtCore.pyqtSignal(str, object)

    def __init__(self, main_software_thread):
        super().__init__()
        self.current_state = {}   # cache – sent to new browsers on connect
        self._chart_buf       = deque(maxlen=1440)   # 4 h × 1 sample/10 s
        self._chart_last_ts   = 0.0
        self._chart_interval  = 10.0                 # seconds between chart samples

        # MainSoftwareThread → WebBridge
        main_software_thread.update_view.connect(self.on_update_view)
        main_software_thread.update_statistics.connect(self.on_update_statistics)
        main_software_thread.update_days_statistics.connect(self.on_update_days_statistics)
        main_software_thread.update_motor.connect(self.on_update_motor)
        main_software_thread.update_spinbox_value.connect(self.on_update_spinbox)
        main_software_thread.update_int_spinbox_value.connect(self.on_update_int_spinbox)
        main_software_thread.update_date_edit.connect(self.on_update_date_edit)
        main_software_thread.update_radio_button_exclusive.connect(self.on_update_radio_button)

        # WebBridge → MainSoftwareThread
        self.button_clicked.connect(main_software_thread.handle_button_click)
        self.float_spinBox_value_changed.connect(main_software_thread.handle_float_spinBox_value)
        self.initialization_step.connect(main_software_thread.handle_intialization_step)
        self.initialization_done.connect(main_software_thread.handle_initialization_done)
        self.radio_button_toggled.connect(main_software_thread.handle_radio_button_toggle)
        self.date_changed.connect(main_software_thread.on_date_received)

        # Send default values (mirrors what MainWindow.__init__ emitted from .ui defaults)
        self._send_init_defaults()
        self._load_chart_history_from_csv()

    def _send_init_defaults(self):
        defaults = [
            ("maxHysteresisValue_temperature_spinBox",      37.8),
            ("minHysteresisValue_temperature_spinBox",      37.5),
            ("maxHysteresisValue_humidity_spinBox",         45.0),
            ("minHysteresisValue_humidity_spinBox",         25.0),
            ("setPointHumidity_spinBox",                    35.0),   # centro della banda qui sopra
            ("maxHysteresisValue_waterLevelControl_spinBox", 4.0),
            ("minHysteresisValue_waterLevelControl_spinBox", 1.0),
            ("setPointTemperature_PID_spinBox",             37.8),
            ("Kp_spinBox",  0.0),
            ("Ki_spinBox",  0.0),
            ("Kd_spinBox",  0.0),
            ("days_duration_spinBox", 0.0),
            ("webRefreshInterval_spinBox", 1000.0),
        ]
        sb = {}
        for name, val in defaults:
            self.initialization_step.emit(name, val)
            sb[name] = val
        self.current_state['spinboxes'] = sb
        self.initialization_done.emit("GUI_initialization_procedure", True)

    @staticmethod
    def _fmt(secs):
        s = int(secs)
        if s < 60:
            return f"{s} sec"
        if s < 3600:
            m, r = divmod(s, 60)
            return f"{m} min {r} sec" if r else f"{m} min"
        h, rem = divmod(s, 3600)
        m = rem // 60
        return f"{h} h {m} min" if m else f"{h} h"

    @staticmethod
    def _safe_temp(v):
        """Return v if it's a plausible temperature, else None (Plotly gap)."""
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        if f in (-127.0, 85.0) or not (5.0 <= f <= 80.0):
            return None
        return round(f, 2)

    def _load_chart_history_from_csv(self):
        """Pre-populate _chart_buf from the last 4 h of saved CSV files."""
        script_dir = os.path.dirname(os.path.abspath(__file__))
        temp_dir   = os.path.join(script_dir, 'Machine_Statistics', 'Temperatures')
        ext_dir    = os.path.join(script_dir, 'Machine_Statistics', 'External_Temperature')

        cutoff = datetime.now() - timedelta(hours=4)
        # build list of dates that might contain rows in the window (spans midnight)
        dates = []
        d = cutoff.date()
        while d <= datetime.now().date():
            dates.append(d.strftime('%Y-%m-%d'))
            d += timedelta(days=1)

        rows = {}   # ts_str → point dict

        for date_str in dates:
            fp = os.path.join(temp_dir, f"{date_str}.csv")
            if not os.path.exists(fp):
                continue
            try:
                with open_with_retry(fp, 'r', newline='') as f:
                    for row in csv.DictReader(f):
                        ts_str = row.get('Timestamp', '')
                        try:
                            ts = datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S')
                        except ValueError:
                            continue
                        if ts < cutoff:
                            continue
                        rows[ts_str] = {
                            'ts': ts.strftime('%Y-%m-%dT%H:%M:%S'),
                            'T1': self._safe_temp(row.get('TMP01')),
                            'T2': self._safe_temp(row.get('TMP02')),
                            'T3': self._safe_temp(row.get('TMP03')),
                            'T4': self._safe_temp(row.get('TMP04')),
                            'Te': None,
                            # assente nei CSV scritti prima dell'aggiunta della colonna
                            'SP': self._safe_temp(row.get('SETPOINT')),
                        }
            except Exception:
                pass

        for date_str in dates:
            fp = os.path.join(ext_dir, f"{date_str}.csv")
            if not os.path.exists(fp):
                continue
            try:
                with open_with_retry(fp, 'r', newline='') as f:
                    for row in csv.DictReader(f):
                        ts_str = row.get('Timestamp', '')
                        try:
                            ts = datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S')
                        except ValueError:
                            continue
                        if ts < cutoff:
                            continue
                        if ts_str in rows:
                            rows[ts_str]['Te'] = self._safe_temp(row.get('EXTT'))
                        else:
                            rows[ts_str] = {
                                'ts': ts.strftime('%Y-%m-%dT%H:%M:%S'),
                                'T1': None, 'T2': None, 'T3': None, 'T4': None,
                                'Te': self._safe_temp(row.get('EXTT')),
                                'SP': None,
                            }
            except Exception:
                pass

        for ts_str in sorted(rows):
            self._chart_buf.append(rows[ts_str])

    # ---- Slots: data FROM MainSoftwareThread → push to browsers ----

    def on_update_view(self, all_data):
        if len(all_data) < 16:
            return
        d = {
            'temp1': all_data[0],  'temp2': all_data[1],
            'temp3': all_data[2],  'temp4': all_data[3],
            'humidity1': all_data[4], 'tempFromHumidity': all_data[5],
            'heatCtrlVal': all_data[6], 'humCtrlVal': all_data[7],
            'heaterStatus': all_data[8], 'humidifierStatus': all_data[9],
            'externalTemp': all_data[10], 'waterWeight': all_data[11],
            'waterCtrlVal': all_data[12], 'evalveStatus': all_data[13],
            'pidCurrentValue': all_data[14], 'pidDutyCycle': all_data[15],
        }
        # Riferimenti di controllo (aggiunti in coda ad all_values: restano
        # opzionali così una view più vecchia non si rompe).
        if len(all_data) >= 19:
            d['setpoint'] = all_data[16]
            d['spMin']    = all_data[17]
            d['spMax']    = all_data[18]
        self.current_state['view'] = d
        socketio.emit('update_view', d)

        # ---- chart sampling (every _chart_interval seconds) ----
        now = time.time()
        if now - self._chart_last_ts >= self._chart_interval:
            self._chart_last_ts = now
            point = {
                'ts': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
                'T1': self._safe_temp(all_data[0]),
                'T2': self._safe_temp(all_data[1]),
                'T3': self._safe_temp(all_data[2]),
                'T4': self._safe_temp(all_data[3]),
                'Te': self._safe_temp(all_data[10]),
                'SP': d.get('setpoint'),
            }
            self._chart_buf.append(point)
            socketio.emit('update_chart', point)

    def on_update_statistics(self, all_data):
        if len(all_data) < 14:
            return
        d = {
            'minTemp':  all_data[0], 'meanTemp': all_data[1], 'maxTemp': all_data[2],
            'onCountT': all_data[3], 'offCountT': all_data[4],
            'timeOnT':  self._fmt(all_data[5]), 'timeOffT': self._fmt(all_data[6]),
            'minHum':   all_data[7], 'meanHum':  all_data[8], 'maxHum':  all_data[9],
            'onCountH': all_data[10], 'offCountH': all_data[11],
            'timeOnH':  self._fmt(all_data[12]), 'timeOffH': self._fmt(all_data[13]),
        }
        self.current_state['statistics'] = d
        socketio.emit('update_statistics', d)

    def on_update_days_statistics(self, all_data):
        if len(all_data) < 2:
            return
        d = {'daysPassed': all_data[0], 'daysLeft': all_data[1]}
        self.current_state['days_statistics'] = d
        socketio.emit('update_days_statistics', d)

    def on_update_motor(self, all_data):
        if len(all_data) < 6:
            return
        d = {
            'timePassed':    self._fmt(all_data[0]),
            'timeToNextTurn': self._fmt(all_data[1]),
            'turnsCounter':  all_data[2],
            'mainState':     all_data[3],
            'manualState':   all_data[4],
            'rotationState': all_data[5],
        }
        self.current_state['motor'] = d
        socketio.emit('update_motor', d)

    def _set_spinbox(self, name, value):
        if 'spinboxes' not in self.current_state:
            self.current_state['spinboxes'] = {}
        self.current_state['spinboxes'][name] = value

    def on_update_spinbox(self, name, value):
        self._set_spinbox(name, value)
        socketio.emit('update_spinbox', {'name': name, 'value': value})

    def on_update_int_spinbox(self, name, value):
        self._set_spinbox(name, value)
        socketio.emit('update_spinbox', {'name': name, 'value': value})

    def on_update_date_edit(self, name, date_val):
        if 'dates' not in self.current_state:
            self.current_state['dates'] = {}
        self.current_state['dates'][name] = date_val.isoformat()
        socketio.emit('update_date', {'name': name, 'date': date_val.isoformat()})

    def on_update_radio_button(self, name, value):
        if 'radio_buttons' not in self.current_state:
            self.current_state['radio_buttons'] = {}
        self.current_state['radio_buttons'][name] = value
        socketio.emit('update_radio', {'name': name, 'checked': value})


# NOTE: MainWindow is kept for reference but is no longer instantiated.
class MainWindow(QtWidgets.QMainWindow):
    # Define custom signals - this is done to send button/spinBox and other custom signals to other thread MainSoftwareThread: use Qt Signals
    button_clicked = QtCore.pyqtSignal(str)  # Emits button name
    float_spinBox_value_changed = QtCore.pyqtSignal(str, float)  # Emits spinbox value  // METTI INT se intero
    initialization_step = QtCore.pyqtSignal(str, float)
    initialization_done = QtCore.pyqtSignal(str, bool)
    radio_button_toggled = QtCore.pyqtSignal(str, bool)
    date_changed = QtCore.pyqtSignal(str, object)   # object = datetime.date - CALENDAR WIDGET
    
    def __init__(self, main_software_thread):
        super().__init__()
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self.main_software_thread = main_software_thread
        self.main_software_thread.update_view.connect(self.update_display_data)
        self.main_software_thread.update_statistics.connect(self.update_statistics_data)
        self.main_software_thread.update_days_statistics.connect(self.update_days_statistics_data)
        self.main_software_thread.update_motor.connect(self.update_display_motor_data)
        self.main_software_thread.update_date_edit.connect(self.update_date_edit)
        
        # Connect signals to main software thread slots
        self.button_clicked.connect(self.main_software_thread.handle_button_click)
        self.float_spinBox_value_changed.connect(self.main_software_thread.handle_float_spinBox_value)
        self.initialization_step.connect(self.main_software_thread.handle_intialization_step)
        self.initialization_done.connect(self.main_software_thread.handle_initialization_done) # signals that MainWindow has completed the initialization procedure (all emit signals have been sent)
        self.main_software_thread.update_spinbox_value.connect(self.update_spinbox)
        self.main_software_thread.update_int_spinbox_value.connect(self.update_int_spinbox)
        self.radio_button_toggled.connect(self.main_software_thread.handle_radio_button_toggle)
        self.date_changed.connect(self.main_software_thread.on_date_received)
        self.main_software_thread.update_radio_button_exclusive.connect(self.update_radio_button_exclusive)


        # Connect buttons to handlers that emit signals
        self.ui.move_CW_motor_btn.clicked.connect(lambda: self.emit_button_signal(self.ui.move_CW_motor_btn.objectName()))
        self.ui.move_CCW_motor_btn.clicked.connect(lambda: self.emit_button_signal(self.ui.move_CCW_motor_btn.objectName()))
        self.ui.layHorizontal_motor_btn.clicked.connect(lambda: self.emit_button_signal(self.ui.layHorizontal_motor_btn.objectName()))
        self.ui.forceEggsTurn_motor_btn.clicked.connect(lambda: self.emit_button_signal(self.ui.forceEggsTurn_motor_btn.objectName()))
        self.ui.reset_statistics_T_btn.clicked.connect(lambda: self.emit_button_signal(self.ui.reset_statistics_T_btn.objectName()))
        
        self.ui.plotMeanTemperature_btn.clicked.connect(lambda: self.emit_button_signal(self.ui.plotMeanTemperature_btn.objectName()))
        self.ui.plotExternalTemperature_btn.clicked.connect(lambda: self.emit_button_signal(self.ui.plotExternalTemperature_btn.objectName()))
        self.ui.plotAllDays_temp_T_btn.clicked.connect(lambda: self.emit_button_signal(self.ui.plotAllDays_temp_T_btn.objectName()))
        self.ui.plotToday_temp_T_btn.clicked.connect(lambda: self.emit_button_signal(self.ui.plotToday_temp_T_btn.objectName()))
        self.ui.plotAllDays_humidity_H_btn.clicked.connect(lambda: self.emit_button_signal(self.ui.plotAllDays_humidity_H_btn.objectName()))
        self.ui.plotToday_humidity_H_btn.clicked.connect(lambda: self.emit_button_signal(self.ui.plotToday_humidity_H_btn.objectName()))
        
        self.ui.plotToday_cnt_H_btn.clicked.connect(lambda: self.emit_button_signal(self.ui.plotToday_cnt_H_btn.objectName()))
        self.ui.plotAllDays_cnt_H_btn.clicked.connect(lambda: self.emit_button_signal(self.ui.plotAllDays_cnt_H_btn.objectName()))

        # Connect radio buttons to emit its values
        # Connect radio buttons to emit signals
        radio_buttons = [
            self.ui.heaterOFF_radioBtn,
            self.ui.heaterAUTO_radioBtn,
            self.ui.heaterON_radioBtn,
            self.ui.humidifierOFF_radioBtn,
            self.ui.humidifierAUTO_radioBtn,
            self.ui.humidifierON_radioBtn,
            self.ui.evalveOFF_radioBtn,
            self.ui.evalveAUTO_radioBtn,
            self.ui.evalveON_radioBtn,
            self.ui.removeErrors_from_T_plots,
            self.ui.removeErrors_from_H_plots,
            self.ui.hysteresisActive_radioBtn,
            self.ui.PIDActive_radioBtn
        ]
        for radio_button in radio_buttons:
            radio_button.toggled.connect(lambda state, btn=radio_button: self.emit_radio_button_signal(btn.objectName(), state))
        
        # Connect spinBox to emit its values
        self.ui.rotation_interval_spinBox.valueChanged.connect(lambda value: self.emit_float_spinbox_signal(self.ui.rotation_interval_spinBox.objectName(), value))
        
        # Temperature Hysteresis
        self.ui.maxHysteresisValue_temperature_spinBox.valueChanged.connect(lambda value: self.emit_float_spinbox_signal(self.ui.maxHysteresisValue_temperature_spinBox.objectName(), value))
        self.ui.minHysteresisValue_temperature_spinBox.valueChanged.connect(lambda value: self.emit_float_spinbox_signal(self.ui.minHysteresisValue_temperature_spinBox.objectName(), value))
        
        # Humidity Hysteresis
        self.ui.maxHysteresisValue_humidity_spinBox.valueChanged.connect(lambda value: self.emit_float_spinbox_signal(self.ui.maxHysteresisValue_humidity_spinBox.objectName(), value))
        self.ui.minHysteresisValue_humidity_spinBox.valueChanged.connect(lambda value: self.emit_float_spinbox_signal(self.ui.minHysteresisValue_humidity_spinBox.objectName(), value))
        
        # Water Level Control Hysteresis
        self.ui.maxHysteresisValue_waterLevelControl_spinBox.valueChanged.connect(lambda value: self.emit_float_spinbox_signal(self.ui.maxHysteresisValue_waterLevelControl_spinBox.objectName(), value))
        self.ui.minHysteresisValue_waterLevelControl_spinBox.valueChanged.connect(lambda value: self.emit_float_spinbox_signal(self.ui.minHysteresisValue_waterLevelControl_spinBox.objectName(), value))
        
        # PID control
        self.ui.setPointTemperature_PID_spinBox.valueChanged.connect(lambda value: self.emit_float_spinbox_signal(self.ui.setPointTemperature_PID_spinBox.objectName(), value))
        self.ui.Kp_spinBox.valueChanged.connect(lambda value: self.emit_float_spinbox_signal(self.ui.Kp_spinBox.objectName(), value))
        self.ui.Ki_spinBox.valueChanged.connect(lambda value: self.emit_float_spinbox_signal(self.ui.Ki_spinBox.objectName(), value))
        self.ui.Kd_spinBox.valueChanged.connect(lambda value: self.emit_float_spinbox_signal(self.ui.Kd_spinBox.objectName(), value))        
        self.ui.days_duration_spinBox.valueChanged.connect(lambda value: self.emit_float_spinbox_signal(self.ui.days_duration_spinBox.objectName(), value))
        
        self.ui.calendarWidget.selectionChanged.connect(self.on_calendar_selection_changed)
        
        # Connect to send initialization values to the mainSoftwareThread
        self.emit_initialization_values(self.ui.maxHysteresisValue_temperature_spinBox.objectName(), self.ui.maxHysteresisValue_temperature_spinBox.value())
        self.emit_initialization_values(self.ui.minHysteresisValue_temperature_spinBox.objectName(), self.ui.minHysteresisValue_temperature_spinBox.value())
        
        self.emit_initialization_values(self.ui.maxHysteresisValue_humidity_spinBox.objectName(), self.ui.maxHysteresisValue_humidity_spinBox.value())
        self.emit_initialization_values(self.ui.minHysteresisValue_humidity_spinBox.objectName(), self.ui.minHysteresisValue_humidity_spinBox.value())
        
        self.emit_initialization_values(self.ui.maxHysteresisValue_waterLevelControl_spinBox.objectName(), self.ui.maxHysteresisValue_waterLevelControl_spinBox.value())
        self.emit_initialization_values(self.ui.minHysteresisValue_waterLevelControl_spinBox.objectName(), self.ui.minHysteresisValue_waterLevelControl_spinBox.value())
        
        # PID control
        self.emit_initialization_values(self.ui.setPointTemperature_PID_spinBox.objectName(), self.ui.setPointTemperature_PID_spinBox.value())
        self.emit_initialization_values(self.ui.Kp_spinBox.objectName(), self.ui.Kp_spinBox.value())
        self.emit_initialization_values(self.ui.Ki_spinBox.objectName(), self.ui.Ki_spinBox.value())
        self.emit_initialization_values(self.ui.Kd_spinBox.objectName(), self.ui.Kd_spinBox.value())
        
        
        self.emit_initialization_values(self.ui.days_duration_spinBox.objectName(), self.ui.days_duration_spinBox.value())
        
        # signaling that ManWindow initialization procedure has been completed
        self.initialization_done.emit("GUI_initialization_procedure", True)


    def emit_initialization_values(self, spinbox_name, value):
        self.initialization_step.emit(spinbox_name, value)
        
        
    def emit_button_signal(self, button_name):
        self.button_clicked.emit(button_name)
         #print(f"Button clicked: {button_name}")

    def emit_float_spinbox_signal(self, spinbox_name, value):
        #value = float(value)  # Cast value to float explicitly
        self.float_spinBox_value_changed.emit(spinbox_name, value)
        #print(f"[MainWindow] Emitting signal from {spinbox_name} with value: {value}")
        
    def emit_radio_button_signal(self, radio_button_name, state):
        self.radio_button_toggled.emit(radio_button_name, state)
        
    def on_calendar_selection_changed(self):
        qdate = self.ui.calendarWidget.selectedDate() # qdate = PyQt5.QtCore.QDate(2025, 12, 26)
        py_date = qdate.toPyDate() # ← chiave  py_date = 2025-12-26
        self.date_changed.emit("incubationStartDate", py_date)
        
    def update_date_edit(self, date_edit_name, date):
        qdate = QtCore.QDate(date.year, date.month, date.day)
        
        date_edit = getattr(self.ui, date_edit_name, None)  # Get the spinbox dynamically
        if date_edit: # ensure it exists
            date_edit.setDate(qdate)

    def update_display_data(self, all_data):
        # Update the temperature labels in the GUI
        if len(all_data) >= 6: # perché il numero??
            self.ui.temperature1_T.setText(f"{all_data[0]} °C")
            self.ui.temperature2_T.setText(f"{all_data[1]} °C")
            self.ui.temperature3_T.setText(f"{all_data[2]} °C")
            self.ui.temperature4_T.setText(f"{all_data[3]} °C")
            self.ui.humidity1_H.setText(f"{all_data[4]} %")
            self.ui.temperatureFromHumidity1.setText(f"{all_data[5]} °C")
            self.ui.heatCtrlVal.setText(f"{all_data[6]} °C")
            self.ui.humCtrlVal.setText(f"{all_data[7]} %")
            if all_data[8] == True:
                self.ui.heaterStatus.setText(f"Heating ON!")
            else:
                self.ui.heaterStatus.setText(f"OFF")
            if all_data[9] == True:
                self.ui.humidifierStatus.setText(f"Humidifying ON!")
            else:
                self.ui.humidifierStatus.setText(f"OFF")
            self.ui.externalTemperature.setText(f"{all_data[10]} °C")
            self.ui.waterTankWeight_1.setText(f"{all_data[11]} kg")
            self.ui.waterLevelControlVal.setText(f"{all_data[12]} kg")
            if all_data[13] == True:
                self.ui.evalveStatus.setText(f"Filling Water ON!")
            else:
                self.ui.evalveStatus.setText(f"OFF")
            self.ui.PID_CurrentValue.setText(f"{all_data[14]} °C")
            self.ui.PID_DutyCycle.setText(f"{all_data[15]} %")
            
            #self.ui.temperature4_2.setText(f"{all_data[3]} °C") PER TEMPERATURA DA UMIDITA
            
    def update_statistics_data(self, all_data):
        # da implementare la parte di update delle statistiche
        if len(all_data) > 0:
            self.ui.minTemp_T.setText(f"{all_data[0]} °C")
            self.ui.meanTemp_T.setText(f"{all_data[1]} °C")
            self.ui.maxTemp_T.setText(f"{all_data[2]} °C")
            self.ui.onCounter_T.setText(f"{all_data[3]}")
			# VISUALIZZAZIONE DEI TEMPI: il programma di base mi manda dei secondi. E' qui che stampo la stringa opportunamente in min o h
            self.ui.timeOn_T.setText(self.format_time(all_data[5]))
            self.ui.timeOFF_T.setText(self.format_time(all_data[6]))
            self.ui.minHum_H.setText(f"{all_data[7]} %")
            self.ui.meanHum_H.setText(f"{all_data[8]} %")
            self.ui.maxHum_H.setText(f"{all_data[9]} %")
            self.ui.onCounter_H.setText(f"{all_data[10]}")
            self.ui.offCounter_H.setText(f"{all_data[11]}")
            self.ui.timeOn_H.setText(self.format_time(all_data[12]))
            self.ui.timeOFF_H.setText(self.format_time(all_data[13]))
            
        pass
    
    def update_days_statistics_data(self, all_data):
        # da implementare la parte di update delle statistiche
        if len(all_data) > 0:
            self.ui.daysPassed.setText(f"{all_data[0]}")
            self.ui.daysLeft.setText(f"{all_data[1]}")
            
        pass
    
    def format_time(self, value, unit = None, simple_format = False):
        """
            Unit argument is optional:
                If unit is "sec", it returns only seconds.
                If unit is "min", it returns only minutes.
                If unit is "hour", it returns only hours.
                If unit is None (default), it follows the mixed format.
        """
        
        if unit == "sec":
            return f"{value} sec"
        elif unit == "min":
            return f"{value // 60} min"
        elif unit == "hour":
            return f"{value // 3600} h"
        
        # Simple format: Only minutes if 60 ≤ value < 3600, only hours if value ≥ 3600
        if simple_format:
            if value >= 3600:
                return f"{value // 3600} h"
            elif value >= 60:
                return f"{value // 60} min"
        
        # Default behavior (detailed format)
        if value < 60:
            return f"{value} sec"
        elif value < 3600:
            minutes = value // 60
            seconds = value % 60
            return f"{minutes} min {seconds} sec" if seconds else f"{minutes} min"
        else:
            hours = value // 3600
            minutes = (value % 3600) // 60
            return f"{hours} h {minutes} min" if minutes else f"{hours} h"

            
    def update_display_motor_data(self, all_data):
        if len(all_data) > 0:
            self.ui.timePassed.setText(self.format_time(all_data[0]))
            self.ui.timeToNextTurn.setText(self.format_time(all_data[1]))
            self.ui.turnsCounter.setText(f"{all_data[2]}")
            self.ui.main_state.setText(f"{all_data[3]}")
            self.ui.manual_state.setText(f"{all_data[4]}")
            self.ui.rotation_state.setText(f"{all_data[5]}")

    def update_spinbox(self, spinbox_name, value):
        """ Update the spinbox in the GUI safely from another thread """
        spinbox = getattr(self.ui, spinbox_name, None)  # Get the spinbox dynamically
        if spinbox:  # Ensure the spinbox exists
            spinbox.setValue(value)  # Set the new value safely in the GUI thread
            
    def update_int_spinbox(self, spinbox_name, value):
        """ 
            Questa funzione è leggermente diversa dal caso prima, perché il valore passato in questo caso dal main thread al thread della GUI
            è di tipo INT! questo è definito nel punto un cui si definiscno i segnali di comunicazione fra i due thread. 
                update_spinbox_value = QtCore.pyqtSignal(str, float) --> fa un cast a float in automatico quando invia i sengali da un thread all'altro
                update_int_spinbox_value = QtCore.pyqtSignal(str, int) --> fa un cast a int in automatico
        """
        spinbox = getattr(self.ui, spinbox_name, None)  # Get the spinbox dynamically
        if spinbox:  # Ensure the spinbox exists
            spinbox.setValue(value)  # Set the new value safely in the GUI thread
            
    def update_radio_button_exclusive(self, radio_button_name, value):
        radio_button = getattr(self.ui, radio_button_name, None)  # Get the spinbox dynamically
        if radio_button:  # Ensure the spinbox exists
            radio_button.setChecked(value)  # Set the new value safely in the GUI thread
    '''       
    def handle_radio_button(self):
        sender = self.sender()
        if sender.isChecked():
            print(f"Radio button '{sender.text()}' selected")
    '''
    def closeEvent(self, event):
        # Ensure the threads are stopped when the window is closed
        self.main_software_thread.stop()
        self.main_software_thread.wait()
        super().closeEvent(event)


if __name__ == "__main__":
    import os
    # Run Qt headlessly (no display needed – only event loop for QThread/signals)
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

    qt_app = QtCore.QCoreApplication(sys.argv)

    main_software_thread = MainSoftwareThread()

    # WebBridge replaces MainWindow: connects threads to the web front-end
    web_bridge = WebBridge(main_software_thread)

    # Flask-SocketIO runs in a daemon thread; Qt event loop runs in the main thread
    flask_thread = threading.Thread(
        target=lambda: socketio.run(
            flask_app, host='0.0.0.0', port=5000,
            debug=False, use_reloader=False
        ),
        daemon=True,
    )
    flask_thread.start()

    def _open_browser():
        import time, subprocess, shutil, webbrowser
        time.sleep(5)  # wait for Flask to bind port 5000
        # Sul Raspberry la pagina viene disegnata dalla macchina che fa anche il
        # controllo, e su un Pi 3 Chromium rasterizza in software: si parte in
        # modalità leggera (niente animazioni continue, grafici semplificati,
        # Plotly caricato solo se si apre una pagina con grafici). Da un PC in
        # rete si usa http://<ip-del-pi>:5000 e si ha la versione completa.
        # La scelta resta memorizzata nel browser: per tornare indietro basta
        # aprire una volta l'indirizzo con ?lite=0
        lite = BROWSER_LITE_MODE if BROWSER_LITE_MODE is not None else sys.platform.startswith('linux')
        url = 'http://localhost:5000/?lite=1' if lite else 'http://localhost:5000'

        # Try Chrome/Chromium with explicit paths (Linux + Windows)
        chrome_candidates = [
            shutil.which('chromium-browser'),
            shutil.which('chromium'),
            shutil.which('google-chrome'),
            r'C:\Program Files\Google\Chrome\Application\chrome.exe',
            r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
        ]
        browser = next((p for p in chrome_candidates if p and shutil.os.path.exists(p)), None)

        if browser:
            subprocess.Popen([
                browser,
                '--new-window',
                '--noerrdialogs',
                '--disable-session-crashed-bubble',
                # Evita il popup "Choose password for new keyring": senza questi flag
                # Chromium tenta di usare gnome-keyring/kwallet e resta bloccato in attesa
                '--password-store=basic',
                '--use-mock-keychain',
                url
            ])
        else:
            # Fallback: use the system default browser (works on Windows, Linux, macOS)
            webbrowser.open(url)

    browser_thread = threading.Thread(target=_open_browser, daemon=True)
    browser_thread.start()

    main_software_thread.start()

    print("Incubator web server started → http://localhost:5000")
    print("Da un altro PC in rete: http://<ip-di-questa-macchina>:5000")
    sys.exit(qt_app.exec_())

    sys.exit(app.exec_())