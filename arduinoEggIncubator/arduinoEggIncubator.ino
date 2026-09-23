/* LIBRARIES */
#include <SoftwareSerial.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <Wire.h>
#include <ProfiloLibrary.h>
#include <DHT.h>
#include "HX711.h"
#include <string.h>
#include <stdlib.h>
#include <stdarg.h>

/* ============================================================================
   PROTEZIONE SUI PIN NON ESISTENTI SULLA SCHEDA IN USO

   Questo sketch è scritto per un Arduino MEGA e usa i pin 22..45 per relè,
   induttori, sensori e segnalazioni. Su un UNO esistono solo i pin 0..19.

   digitalWrite() del core AVR NON controlla il range: indicizza tabelle in
   PROGMEM dimensionate su NUM_DIGITAL_PINS e, con un pin fuori scala, ricava un
   puntatore casuale e ci scrive dentro. Il caso HIGH esegue *out |= bit, quindi
   ACCENDE un bit in una locazione arbitraria (registro di I/O, stack pointer,
   variabili): la scheda si corrompe e riparte. Il caso LOW esegue *out &= ~bit,
   che sulla stessa locazione il più delle volte non cambia nulla.

   È esattamente l'asimmetria osservata: @<HTR01, True># riavvia la scheda ogni
   volta, @<HTR01, False># non lo fa mai, e PWM01 (pin 13, valido anche su UNO)
   non ha mai dato problemi.

   Con questi wrapper, su una scheda piccola i pin inesistenti vengono ignorati
   invece di corrompere la memoria: la simulazione su UNO gira senza danni e sul
   Mega il comportamento resta identico a prima.
   ============================================================================ */
inline bool pinExistsOnThisBoard(uint8_t pin) {
  return pin < NUM_DIGITAL_PINS;
}

inline void safePinMode(uint8_t pin, uint8_t mode) {
  if (!pinExistsOnThisBoard(pin)) return;
  pinMode(pin, mode);
}

inline void safeDigitalWrite(uint8_t pin, uint8_t val) {
  if (!pinExistsOnThisBoard(pin)) return;
  digitalWrite(pin, val);
}

inline int safeDigitalRead(uint8_t pin) {
  if (!pinExistsOnThisBoard(pin)) return LOW;
  return digitalRead(pin);
}

inline void safeAnalogWrite(uint8_t pin, int val) {
  if (!pinExistsOnThisBoard(pin)) return;
  analogWrite(pin, val);
}

/* Da qui in poi le chiamate del resto dello sketch passano dai wrapper.
   Le macro sono definite DOPO le funzioni, così i wrapper chiamano le versioni
   vere del core e non se stessi. */
#define pinMode(p, m)      safePinMode((p), (m))
#define digitalWrite(p, v) safeDigitalWrite((p), (v))
#define digitalRead(p)     safeDigitalRead((p))
#define analogWrite(p, v)  safeAnalogWrite((p), (v))

#if NUM_DIGITAL_PINS < 46
#warning "Scheda con meno di 46 pin (es. Arduino UNO): rele', induttori e segnalazioni sui pin 22..45 sono INERTI. Va bene per la simulazione, NON per la macchina reale, che richiede un Mega."
#endif

/* General CONSTANTS */
#define SIMULATION true  // Set to 'true' to run without hardware (all sensor values are simulated)
#define SERIAL_PRINT_CHECK false
#define SERIAL_SPEED 115200
#define NUMBER_OF_TEMPERATURES_SENSORS_ON_ONE_WIRE_BUS 4 //sensori di temperatura
#define DEFAULT_DEBOUNCE_TIME 25 //ms
#define ENABLE_HEATER true
#define ENABLE_HUMIDIFIER true
#define ENABLE_WATER_ELECTROVALVE true
#define TEMPERATURE_PRECISION 9 // DS18B20 digital termometer provides 9-bit to 12-bit Celsius temperature measurements
#define NUMBER_OF_LIGHTS 3
#if !defined(DEVICE_DISCONNECTED)
#define DEVICE_DISCONNECTED -127
#endif
#define DEVICE_ERROR 85
#define DC_MOTOR_ACTIVATED true

/* Simulation constants */
#define SIM_SENSOR_MS  500UL   // sensor-update cadence in simulation [ms]
#define SIM_TRAVEL_MS 4000UL   // simulated motor travel time to reach an end stop [ms]

/* ARDUINO MEGA PWM from 0 - 13 + 0 and 1 tx and rx in case of serial communication is needed */
/* PIN ARDUINO */
#define DHT_PIN 24   //Pin a cui è connesso il sensore
#define ONE_WIRE_BUS 26
#define STEPPER_MOTOR_DIRECTION_PIN 5
#define STEPPER_MOTOR_STEP_PIN 6
#define FAN_PWM_PIN 7 // ventole ricircolo aria: PWM 0-100% (pin libero, ex FREE_PC817_PIN, mai usato)
#define CW_INDUCTOR_PIN 45 // induttore finecorsa DESTRO (vista posteriore)
#define CCW_INDUCTOR_PIN 43 // induttore finecorsa SINISTRO (vista posteriore)
#define WATER_ELECTROVALVE_PIN 41 // relay 4
#define HUMIDIFIER_PIN 39 // relay 3
#define HEATER_PIN 37 // relay 1
#define HEATER_PWM_PIN 13

#define ONE_WIRE_BUS_EXTERNAL_TEMPERATURE 22
#define FREE_OUTPUT_RELAY_PC817_2 23
#define PIN_RED_LIGHT 25
#define PIN_ORANGE_LIGHT 27
#define PIN_GREEN_LIGHT 29
#define PIN_BUZZER 31
#define LOADCELL_DOUT_PIN 33
#define LOADCELL_SCK_PIN 35

#define DC_MOTOR_1_IN1 11 //L980N pin IN1
#define DC_MOTOR_1_IN2 10 //L980N pin IN2
#define DC_MOTOR_2_IN1 9  //L980N pin IN3
#define DC_MOTOR_2_IN2 8  //L980N pin IN4
#define MOTOR_SPEED 50    // Duty cycle: 0 (fermo) … 255 (massima velocità)

#define STEPPER_MOTOR_MS1_PIN 2
#define STEPPER_MOTOR_MS2_PIN 2
#define STEPPER_MOTOR_MS3_PIN 2


/* ALIVE su SERIALE */
bool alive_bit = false;
unsigned long last_serial_alive_time = 0;
unsigned long serial_alive_timeout_ms = 4000;
bool serial_communication_is_ok = false;

/* TEMPERATURES SECTION */
// GENERAL
bool deviceOrderingActive = true;
float marginFactor = 5;
unsigned long startGetTemperatures;

OneWire oneWire(ONE_WIRE_BUS);
DallasTemperature sensors(&oneWire);
DeviceAddress Thermometer[NUMBER_OF_TEMPERATURES_SENSORS_ON_ONE_WIRE_BUS];
byte numberOfDevices;
unsigned long conversionTime_DS18B20_sensors;
unsigned long lastTempRequest;

char temperatureSensor_address0[] = "28FF640E7213DCBE";
char temperatureSensor_address1[] = "28FF640E7C2E42E0";
char temperatureSensor_address2[] = "28FF640E7F7492C3";
char temperatureSensor_address3[] = "28FF640E7F489F5E";

unsigned int deviceDisconnected[NUMBER_OF_TEMPERATURES_SENSORS_ON_ONE_WIRE_BUS];
unsigned int deviceError[NUMBER_OF_TEMPERATURES_SENSORS_ON_ONE_WIRE_BUS];

float temperatures[4];
bool gotTemperatures;

DeviceAddress tempDeviceAddress;
char addressCharArray[17];

// EXTERNAL TEMPERATURE SENSOR
#define ENABLE_EXTERNAL_TEMPERATURE_READING true
OneWire oneWire_externalTemperatureSensor(ONE_WIRE_BUS_EXTERNAL_TEMPERATURE);
DallasTemperature externalTemperatureSensor(&oneWire_externalTemperatureSensor);
DeviceAddress externalTemperatureSensor_address;

unsigned int deviceDisconnected_externalTemperatureSensor;
unsigned int deviceError_externalTemperatureSensor;

float temperature_externalTemperatureSensor;
/* END TEMPERATURES SECTION */

/* HEATER SECTION */
const unsigned long WINDOW_MS = 10000;
const unsigned long FAILSAFE_MS = 30000;

float u_cmd = 0.0f;
unsigned long windowStart = 0;
unsigned long lastCmdMs = 0;
/* END HEATER SECTION */

/* FAN SECTION - ventole ricircolo aria: PWM diretto (non è un SSR, niente finestra software) */
float fan_speed_cmd = 0.0f; // 0.0 (spente) ... 1.0 (velocità massima)
/* END FAN SECTION */

/* CCW_LS */
antiDebounceInput ccw_inductor_input(CCW_INDUCTOR_PIN, DEFAULT_DEBOUNCE_TIME);

/* CW_LS */
antiDebounceInput cw_inductor_input(CW_INDUCTOR_PIN, DEFAULT_DEBOUNCE_TIME);

/*
   Fronte di salita dei finecorsa rilevato a mano (non più con
   trigger::catchRisingEdge()): vedi il commento nel loop() dove sono usate.
*/
bool _prevCCWLimit = false;
bool _prevCWLimit  = false;


/* MOTORS SECTION */
#define STEPPER_MOTOR_SPEED_DEFAULT 10

float stepper_motor_speed = STEPPER_MOTOR_SPEED_DEFAULT;

stepperMotor eggsTurnerStepperMotor(STEPPER_MOTOR_STEP_PIN, STEPPER_MOTOR_DIRECTION_PIN, STEPPER_MOTOR_MS1_PIN, STEPPER_MOTOR_MS2_PIN, STEPPER_MOTOR_MS3_PIN, 1.8);

bool move = false;
bool direction = false;
bool stepperIsMoving = false;

typedef enum {
  STOPPED_STATUS  = 0,
  CW_STATUS       = 1,
  CCW_STATUS      = 2
} MotorState;

MotorState current_DC_motorState_1 = STOPPED_STATUS;
MotorState current_DC_motorState_2 = STOPPED_STATUS;

bool motor_moveCCW_automatic_var = false;
bool motor_moveCW_automatic_var = false;
bool motor_stop_automatic_var = false;

bool motor_moveCCW_cmd = false;
trigger motor_moveCCW_cmd_trigger;

bool motor_moveCW_cmd = false;
trigger motor_moveCW_cmd_trigger;

bool motor_stop_cmd = false;
trigger motor_stop_cmd_trigger;

byte eggsTurnerState = 0;
bool motorAutomaticControl_var = false;

/* END MOTORS SECTION */

/* DHT22 HUMIDITY SENSOR */
#define DHT_TYPE DHT22
DHT dht(DHT_PIN, DHT_TYPE);

int chk;
float humidity_fromDHT22;
float temp_fromDHT22;
/* END DHT22 HUMIDITY SENSOR */

/* HX711 WEIGHT CONTROL LOAD CELL */
HX711 scale;
int32_t calibration_offset = 39679;
float calibration_scale = 421.390777f;
float waterWeight;

const unsigned long waterWeight_timeInterval = 10000;
unsigned long last_waterWeight_measurementTime;

float waterWeight_saturated = 5000.0;
/* END HX711 WEIGHT CONTROL LOAD CELL */


/* MACHINE SINGALING DEVICE - SECTION */
const int lightPins[NUMBER_OF_LIGHTS] = {PIN_RED_LIGHT, PIN_ORANGE_LIGHT, PIN_GREEN_LIGHT};
const int buzzerPin = PIN_BUZZER;

unsigned long lastUpdate = 0;
const unsigned long runInterval = 100;

enum State { OFF, ON, FLASH_FAST, FLASH_SLOW, BEEP_FAST, BEEP_SLOW };

struct Device {
    State state;
    unsigned long lastToggle;
    bool currentState;
};

Device lights[NUMBER_OF_LIGHTS];
Device buzzer;
/* END MACHINE SINGALING DEVICE - SECTION */

// SENDING TO RPY
/*
   Niente più String qui: su un UNO (2 KB di RAM) l'allocazione/deallocazione
   continua di String per ogni tag in uscita e ogni comando in ingresso
   frammentava l'heap. Sintomo osservato: il primo carattere di uno dei tag
   in uscita arrivava sistematicamente sovrascritto con il primo carattere di
   un tag diverso già transitato (es. "WGT01" -> "SGT01", "S" da "STPR01") -
   classico effetto di un blocco di heap riciclato senza essere riscritto per
   intero. Con buffer char a dimensione fissa non c'è più heap da frammentare.
*/
#define MAX_NUMBER_OF_COMMANDS_TO_BOARD 20
#define OUTGOING_ITEM_LEN 24   // un token "<TAG,valore>" in uscita, incluso il terminatore
#define INCOMING_CMD_LEN  32   // il contenuto fra < e > di un comando in ingresso
#define TAG_LEN   10
#define VALUE_LEN 16
#define UID_LEN   12
#define ACK_LEN   48   // "<" + tag + ", " + value + ", " + uid + ">" (vedi TAG/VALUE/UID_LEN)

bool receivingDataFromBoard = false;
char listofDataToSend[MAX_NUMBER_OF_COMMANDS_TO_BOARD][OUTGOING_ITEM_LEN];
byte listofDataToSend_numberOfData = 0;

char fbuffChar[16];   // scratch per dtostrf(), prima di comporre il token con queueOutgoing()

char receivedCommands[MAX_NUMBER_OF_COMMANDS_TO_BOARD][INCOMING_CMD_LEN];

unsigned long last_cycle_time, cycle_time;

/* ============================================================
   SIMULATION MODE – state variables
   ============================================================ */
#if SIMULATION
static float         _sim_wgt_g    = 1500.0f;  // initial water weight [g]
static bool          _sim_ccw      = true;       // start at CCW (home) limit
static bool          _sim_cw       = false;
static bool          _sim_moving   = false;
static bool          _sim_dir_cw   = false;
static unsigned long _sim_move_t0  = 0UL;
static unsigned long _sim_last_upd = 0UL;
#endif


// ============================================================
// Forward declarations
// ============================================================
int  freeRam();
void byteToHex(uint8_t byteValue, char *hexValue);
void addressToCharArray(DeviceAddress deviceAddress, char *charArray);
int  readFromBoard();
void updateDevice(Device &device, int pin, unsigned long fastInterval, unsigned long slowInterval);
void queueOutgoing(const char *fmt, ...);
void trimInPlace(char *s);
bool splitCommand(const char *input,
                   char *tag, size_t tagSize,
                   char *value, size_t valueSize,
                   char *uid, size_t uidSize);
void motorCW(int pin_in1, int pin_in2);
void motorCCW(int pin_in1, int pin_in2);
void motorStop(int pin_in1, int pin_in2);
bool getCCW();
bool getCW();
#if SIMULATION
void sim_motor_tick();
void sim_generateSensors();
bool sim_getCCW();
bool sim_getCW();
void sim_startCW();
void sim_startCCW();
#endif


void setup() {
  pinMode(HEATER_PWM_PIN, OUTPUT);
  digitalWrite(HEATER_PWM_PIN, LOW);

  pinMode(FAN_PWM_PIN, OUTPUT);
  digitalWrite(FAN_PWM_PIN, LOW);

  pinMode(HEATER_PIN, OUTPUT);
  digitalWrite(HEATER_PIN, LOW);

  pinMode(HUMIDIFIER_PIN, OUTPUT);
  digitalWrite(HUMIDIFIER_PIN, LOW);

  pinMode(WATER_ELECTROVALVE_PIN, OUTPUT);
  digitalWrite(WATER_ELECTROVALVE_PIN, LOW);

  pinMode(FREE_OUTPUT_RELAY_PC817_2, OUTPUT);
  digitalWrite(FREE_OUTPUT_RELAY_PC817_2, LOW);

  pinMode(STEPPER_MOTOR_STEP_PIN, OUTPUT);
  digitalWrite(STEPPER_MOTOR_STEP_PIN, LOW);

  pinMode(STEPPER_MOTOR_DIRECTION_PIN, OUTPUT);
  digitalWrite(STEPPER_MOTOR_DIRECTION_PIN, LOW);

  pinMode(STEPPER_MOTOR_MS1_PIN, OUTPUT);
  digitalWrite(STEPPER_MOTOR_MS1_PIN, LOW);

  pinMode(STEPPER_MOTOR_MS2_PIN, OUTPUT);
  digitalWrite(STEPPER_MOTOR_MS2_PIN, LOW);

  pinMode(STEPPER_MOTOR_MS3_PIN, OUTPUT);
  digitalWrite(STEPPER_MOTOR_MS3_PIN, LOW);

  // DC MOTOR
  pinMode(DC_MOTOR_1_IN1, OUTPUT);
  digitalWrite(DC_MOTOR_1_IN1, LOW);

  pinMode(DC_MOTOR_1_IN2, OUTPUT);
  digitalWrite(DC_MOTOR_1_IN2, LOW);

  pinMode(DC_MOTOR_2_IN1, OUTPUT);
  digitalWrite(DC_MOTOR_2_IN1, LOW);

  pinMode(DC_MOTOR_2_IN2, OUTPUT);
  digitalWrite(DC_MOTOR_2_IN2, LOW);

  motorStop(DC_MOTOR_1_IN1, DC_MOTOR_1_IN2);

  pinMode(CCW_INDUCTOR_PIN, INPUT);
  pinMode(CW_INDUCTOR_PIN, INPUT);

  /* MACHINE SINGALING DEVICE - SECTION */
  for (int i = 0; i < NUMBER_OF_LIGHTS; i++) {
        pinMode(lightPins[i], OUTPUT);
        digitalWrite(lightPins[i], LOW);
        lights[i] = {OFF, 0, LOW};
    }
    pinMode(buzzerPin, OUTPUT);
    digitalWrite(buzzerPin, LOW);
    buzzer = {OFF, 0, LOW};

  Serial.begin(SERIAL_SPEED);

#if SIMULATION
  /* ---- Simulation init: skip all sensor hardware ---- */
  randomSeed(analogRead(A0));
  conversionTime_DS18B20_sensors = 0;
  lastTempRequest   = millis();
  _sim_last_upd     = millis();
  last_waterWeight_measurementTime = millis();
  waterWeight       = _sim_wgt_g;
  gotTemperatures   = false;
  sim_generateSensors();
  gotTemperatures   = true;
#else
  /* HX 711 initializing water scale */
  scale.begin(LOADCELL_DOUT_PIN, LOADCELL_SCK_PIN);
  scale.set_offset(calibration_offset);
  scale.set_scale(calibration_scale);

  sensors.begin();
  numberOfDevices = sensors.getDeviceCount();

  sensors.setWaitForConversion(false);
  sensors.requestTemperatures();

  if (ENABLE_EXTERNAL_TEMPERATURE_READING){
    externalTemperatureSensor.begin();
    externalTemperatureSensor.setWaitForConversion(false);
    externalTemperatureSensor.requestTemperatures();
  }

  lastTempRequest = millis();
  conversionTime_DS18B20_sensors = 750 / (1 << (12 - TEMPERATURE_PRECISION));

  for(uint8_t index = 0; index < numberOfDevices; index++){
    if(sensors.getAddress(tempDeviceAddress, index)){
      addressToCharArray(tempDeviceAddress, addressCharArray);

      if(strcmp(addressCharArray, temperatureSensor_address0) == 0){
        sensors.getAddress(Thermometer[0], index);
      }
      else if(strcmp(addressCharArray, temperatureSensor_address1) == 0){
        sensors.getAddress(Thermometer[1], index);
      }
      else if(strcmp(addressCharArray, temperatureSensor_address2) == 0){
        sensors.getAddress(Thermometer[2], index);
      }
      else if(strcmp(addressCharArray, temperatureSensor_address3) == 0){
        sensors.getAddress(Thermometer[3], index);
      }
      else{
        deviceOrderingActive = false;
      }

      deviceDisconnected[index] = 0;
      deviceError[index] = 0;
      delay(5);
    }
  }

  if(!deviceOrderingActive){
    for(uint8_t index = 0; index < numberOfDevices; index++){
      sensors.getAddress(Thermometer[index], index);
      deviceDisconnected[index] = 0;
      deviceError[index] = 0;
    }
  }

  if (ENABLE_EXTERNAL_TEMPERATURE_READING){
    if(externalTemperatureSensor.getAddress(tempDeviceAddress, 0)){
      addressToCharArray(tempDeviceAddress, addressCharArray);
      externalTemperatureSensor.getAddress(externalTemperatureSensor_address, 0);
      deviceDisconnected_externalTemperatureSensor = 0;
      deviceError_externalTemperatureSensor = 0;
    }
  }

  dht.begin();
  last_waterWeight_measurementTime = millis();
  waterWeight = waterWeight_saturated;
#endif // SIMULATION

  last_serial_alive_time = millis();
  cycle_time = millis();
  last_cycle_time = cycle_time;
  windowStart = millis();
  lastCmdMs = millis();

  /*
     Marker di riavvio.
     A questo punto tutte le uscite sono a riposo (relè LOW, u_cmd = 0.0) e il PC
     non lo sa: se non gli si dice nulla continua a credere che gli attuatori siano
     nello stato di prima e, non rinviando comandi a valore invariato, non li
     ripristina mai. Va stampato fuori dalla guardia serial_communication_is_ok,
     che a questo punto è ancora false.
  */
  Serial.print('@');
  Serial.print("<BOOT, 1>");
  Serial.println('#');
}

void loop() {
  // RECEIVING FROM RPI
  if(Serial.available() > 0){
    int numberOfCommandsFromBoard = readFromBoard();
    last_serial_alive_time = millis();
    char pendingACK[ACK_LEN] = "";
    for (byte j = 0; j < numberOfCommandsFromBoard; j++) {
      char tag[TAG_LEN], value[VALUE_LEN], uid[UID_LEN];
      if (splitCommand(receivedCommands[j], tag, sizeof(tag), value, sizeof(value), uid, sizeof(uid))) {
        if (strcmp(tag, "ALIVE") == 0) {
          last_serial_alive_time = millis();
          if (strcmp(value, "True") == 0) {
            alive_bit = true;
            serial_communication_is_ok = true;
          } else if (strcmp(value, "False") == 0) {
            alive_bit = false;
            serial_communication_is_ok = true;
          }
        }

        if (strcmp(tag, "HTR01") == 0) {
          if (strcmp(value, "True") == 0) {
            digitalWrite(HEATER_PIN, HIGH);
          } else if (strcmp(value, "False") == 0) {
            digitalWrite(HEATER_PIN, LOW);
          }
          if(strlen(uid) > 0){
              snprintf(pendingACK, sizeof(pendingACK), "<%s, %s, %s>", tag, value, uid);
          }
        }

        if (strcmp(tag, "HUMER01") == 0) {
          if (strcmp(value, "True") == 0) {
            digitalWrite(HUMIDIFIER_PIN, HIGH);
          } else if (strcmp(value, "False") == 0) {
            digitalWrite(HUMIDIFIER_PIN, LOW);
          }
          if(strlen(uid) > 0){
              snprintf(pendingACK, sizeof(pendingACK), "<%s, %s, %s>", tag, value, uid);
          }
        }

        if (strcmp(tag, "STPR01") == 0) {
          if (strcmp(value, "MCCW") == 0) {
            motor_moveCCW_automatic_var = true;
            if (DC_MOTOR_ACTIVATED)
            motor_moveCCW_cmd = true;
          } else if (strcmp(value, "MCW") == 0) {
            motor_moveCW_automatic_var = true;
            motor_moveCW_cmd = true;
          } else if (strcmp(value, "STOP") == 0) {
            motor_stop_automatic_var = true;
            motor_stop_cmd = true;
          }
          if(strlen(uid) > 0){
              snprintf(pendingACK, sizeof(pendingACK), "<%s, %s, %s>", tag, value, uid);
          }
        }

        if (strcmp(tag, "ELV01") == 0) {
          if (strcmp(value, "True") == 0) {
            digitalWrite(WATER_ELECTROVALVE_PIN, HIGH);
          } else if (strcmp(value, "False") == 0) {
            digitalWrite(WATER_ELECTROVALVE_PIN, LOW);
          }
          if(strlen(uid) > 0){
              snprintf(pendingACK, sizeof(pendingACK), "<%s, %s, %s>", tag, value, uid);
          }
        }

        if (strcmp(tag, "PWM01") == 0) {
          float u = atof(value);
          if (u < 0.0f) u = 0.0f;
          if (u > 1.0f) u = 1.0f;
          u_cmd = u;
          lastCmdMs = millis();   // alimenta il failsafe della sezione HEATER
          if(strlen(uid) > 0){
              snprintf(pendingACK, sizeof(pendingACK), "<%s, %s, %s>", tag, value, uid);
          }
        }

        if (strcmp(tag, "FAN01") == 0) {
          float f = atof(value);
          if (f < 0.0f) f = 0.0f;
          if (f > 1.0f) f = 1.0f;
          fan_speed_cmd = f;
          analogWrite(FAN_PWM_PIN, (int)(fan_speed_cmd * 255.0f + 0.5f));
          if(strlen(uid) > 0){
              snprintf(pendingACK, sizeof(pendingACK), "<%s, %s, %s>", tag, value, uid);
          }
        }
      }
      else{
        continue;
      }
    }
    if(pendingACK[0] != '\0'){
        Serial.print('@');
        Serial.print(pendingACK);
        Serial.println('#');
        pendingACK[0] = '\0';
        delay(1);
    }
  }
  else{
    if(millis() - last_serial_alive_time > serial_alive_timeout_ms){
      serial_communication_is_ok = false;
      eggsTurnerStepperMotor.stopMotor();
      digitalWrite(HEATER_PIN, LOW);
      digitalWrite(HUMIDIFIER_PIN, LOW);
      digitalWrite(WATER_ELECTROVALVE_PIN, LOW);
      analogWrite(HEATER_PWM_PIN, 0);
      analogWrite(FAN_PWM_PIN, 0);
      fan_speed_cmd = 0.0f;
    }
  }

  /* TEMPERATURES SECTION */
#if SIMULATION
  if (millis() - _sim_last_upd >= SIM_SENSOR_MS) {
    sim_generateSensors();   // updates temperatures[], humidity_fromDHT22, temp_fromDHT22,
                             //            temperature_externalTemperatureSensor, waterWeight
    gotTemperatures = true;
    _sim_last_upd = millis();
  }
#else
  if(millis() - lastTempRequest >= (conversionTime_DS18B20_sensors * marginFactor)){
    startGetTemperatures = millis();
    if (SERIAL_PRINT_CHECK){
      Serial.print("Time passed btw two T readings: ");
      Serial.print(millis()-lastTempRequest);
    }
    for(uint8_t index = 0; index < numberOfDevices; index++){
      temperatures[index] = sensors.getTempC(Thermometer[index]);

      if(temperatures[index] <= DEVICE_DISCONNECTED){
        deviceDisconnected[index] ++;
      }
      if(temperatures[index] >= DEVICE_ERROR){
        deviceError[index] ++;
      }
    }

    gotTemperatures = true;
    sensors.requestTemperatures();

    if (ENABLE_EXTERNAL_TEMPERATURE_READING){
      temperature_externalTemperatureSensor = externalTemperatureSensor.getTempC(externalTemperatureSensor_address);
      if(temperature_externalTemperatureSensor <= DEVICE_DISCONNECTED){
        deviceDisconnected_externalTemperatureSensor ++;
      }
      if(temperature_externalTemperatureSensor >= DEVICE_ERROR){
        deviceError_externalTemperatureSensor ++;
      }
      externalTemperatureSensor.requestTemperatures();
    }

    lastTempRequest = millis();

    if (SERIAL_PRINT_CHECK){
      Serial.print(" Time needed to read temperatures: ");
      Serial.println(lastTempRequest - startGetTemperatures);
    }
  }
#endif
  /* END TEMPERATURES SECTION */

  /* HEATER SECTION */
  unsigned long now = millis();

  /*
     Failsafe: se il PC smette di aggiornare il duty (crash, cavo staccato, thread
     bloccato) il riscaldatore non deve restare acceso con l'ultimo valore ricevuto.
     Il PC rinvia PWM01 ogni 5 s anche a valore invariato, quindi FAILSAFE_MS = 30 s
     è un margine ampio: scatta solo se la comunicazione è davvero interrotta.
  */
  if (now - lastCmdMs > FAILSAFE_MS) {
    u_cmd = 0.0f;
  }

  if (now - windowStart >= WINDOW_MS) {
    windowStart = now;
  }
  unsigned long onTime = (unsigned long)(u_cmd * WINDOW_MS);
  bool heaterOn = ((now - windowStart) < onTime);
  digitalWrite(HEATER_PWM_PIN, heaterOn ? HIGH : LOW);


  /* HX 711 WATER WEIGHT MEASUREMENT - LOAD CELL */
  /* In simulation, waterWeight is updated by sim_generateSensors() every SIM_SENSOR_MS */
#if !SIMULATION
  if (stepperIsMoving && (!DC_MOTOR_ACTIVATED)){
    waterWeight = waterWeight_saturated;
  }
  else{
    if ((millis() - last_waterWeight_measurementTime) >= waterWeight_timeInterval){
      if (gotTemperatures){
        scale.power_up();
        waterWeight = scale.get_units(5);
        waterWeight = round(waterWeight * 10.0) / 10.0;
        scale.power_down();
        last_waterWeight_measurementTime = millis();
      }
    }
  }
#endif

  /* DHT22 HUMIDITY SENSOR */
  /* In simulation, humidity_fromDHT22 and temp_fromDHT22 are updated by sim_generateSensors() */
#if !SIMULATION
  humidity_fromDHT22 = dht.readHumidity();
  temp_fromDHT22 = dht.readTemperature();
#endif
  /* END DHT22 HUMIDITY SENSOR */

  /* INDUCTOR INPUT SECTION */
#if SIMULATION
  sim_motor_tick();  // advance motor travel timer and update virtual limit flags
#else
  ccw_inductor_input.periodicRun();
  cw_inductor_input.periodicRun();
#endif
  /* END INDUCTOR INPUT SECTION */

  /*
     Notifica al PC quando un finecorsa viene raggiunto, una volta sola per
     arrivo. Prima usava trigger::catchRisingEdge() (libreria esterna,
     ProfiloLibrary, sorgente non disponibile in questo repo): una volta
     raggiunto un finecorsa getCCW()/getCW() restano "true" finché non
     riparte un movimento nella direzione opposta, e se quella libreria si
     comportasse come un rilevamento di livello invece che di fronte,
     riaccoderebbe <IND_CCW,1>/<IND_CW,1> a ogni giro di loop() invece che una
     volta sola. Sintomo osservato: flusso continuo di ACK "IND_CW"/"IND_CCW"
     pur avendo inviato un solo comando STPR01. Il confronto col valore
     precedente qui sotto è autosufficiente, non dipende da quella libreria.
  */
  bool nowCCW = getCCW();
  bool nowCW  = getCW();
  if(nowCCW && !_prevCCWLimit){
    queueOutgoing("<IND_CCW, 1>");
  }
  if(nowCW && !_prevCWLimit){
    queueOutgoing("<IND_CW, 1>");
  }
  _prevCCWLimit = nowCCW;
  _prevCWLimit  = nowCW;

  /* STEPPER MOTOR CONTROL SECTION */
  if (!DC_MOTOR_ACTIVATED){
    eggsTurnerStepperMotor.periodicRun();
    stepperIsMoving = eggsTurnerStepperMotor.isMovingBackward() || eggsTurnerStepperMotor.isMovingForward();
  }

  switch(eggsTurnerState){
    case 0:
      if(serial_communication_is_ok){
        eggsTurnerState = 1;
      }
      break;
    case 1:
      if(millis() >= 5000){
        eggsTurnerState = 9;
      }
      break;
    case 9: // ZEROING
      if(getCCW()){
        queueOutgoing("<IND_CCW, 1>");

        if (DC_MOTOR_ACTIVATED){
          motorStop(DC_MOTOR_1_IN1, DC_MOTOR_1_IN2);
        }
        else{
          eggsTurnerStepperMotor.stopMotor();
        }

        eggsTurnerState = 20;
      }
      else if(getCW()){
        queueOutgoing("<IND_CW, 1>");

        if (DC_MOTOR_ACTIVATED){
          motorStop(DC_MOTOR_1_IN1, DC_MOTOR_1_IN2);
        }
        else{
          eggsTurnerStepperMotor.stopMotor();
        }

        eggsTurnerState = 20;
      }
      else{
        if (DC_MOTOR_ACTIVATED){
          motorCCW(DC_MOTOR_1_IN1, DC_MOTOR_1_IN2);
          current_DC_motorState_1 = CCW_STATUS;
#if SIMULATION
          sim_startCCW();
#endif
        }
        else{
          eggsTurnerStepperMotor.moveBackward(STEPPER_MOTOR_SPEED_DEFAULT);
        }

        eggsTurnerState = 10;
      }
      break;
    case 10: // WAIT_TO_REACH_LEFT_SIDE_INDUCTOR
      if(getCCW()){
        if (DC_MOTOR_ACTIVATED){
          motorStop(DC_MOTOR_1_IN1, DC_MOTOR_1_IN2);
        }
        else{
          eggsTurnerStepperMotor.stopMotor();
        }
        eggsTurnerState = 20;
      }
      break;
    case 20:
      if(motorAutomaticControl_var){
        if(motor_moveCCW_automatic_var){
          if (DC_MOTOR_ACTIVATED){
            motorCCW(DC_MOTOR_1_IN1, DC_MOTOR_1_IN2);
            current_DC_motorState_1 = CCW_STATUS;
#if SIMULATION
            sim_startCCW();
#endif
          }
          else{
            eggsTurnerStepperMotor.moveBackward(STEPPER_MOTOR_SPEED_DEFAULT);
          }
          eggsTurnerState = 50;
        }
        if(motor_moveCW_automatic_var){
          if (DC_MOTOR_ACTIVATED){
            motorCW(DC_MOTOR_1_IN1, DC_MOTOR_1_IN2);
            current_DC_motorState_1 = CW_STATUS;
#if SIMULATION
            sim_startCW();
#endif
          }
          else{
            eggsTurnerStepperMotor.moveForward(STEPPER_MOTOR_SPEED_DEFAULT);
          }
          eggsTurnerState = 30;
        }
        if(motor_stop_automatic_var){
          if (DC_MOTOR_ACTIVATED){
            motorStop(DC_MOTOR_1_IN1, DC_MOTOR_1_IN2);
          }
          else{
            eggsTurnerStepperMotor.stopMotor();
          }
        }
      }
      else{
        eggsTurnerState = 100;
      }
      break;
    case 30: // WAIT_TO_REACH_RIGHT_SIDE_INDUCTOR
        if(getCW()){
          if (DC_MOTOR_ACTIVATED){
            motorStop(DC_MOTOR_1_IN1, DC_MOTOR_1_IN2);
          }
          else{
            eggsTurnerStepperMotor.stopMotor();
          }
          eggsTurnerState = 20;
        }
      break;
    case 50: // WAIT_TO_REACH_LEFT_SIDE_INDUCTOR
        if(getCCW()){
          if (DC_MOTOR_ACTIVATED){
            motorStop(DC_MOTOR_1_IN1, DC_MOTOR_1_IN2);
          }
          else{
            eggsTurnerStepperMotor.stopMotor();
          }
          eggsTurnerState = 20;
        }
      break;
    case 100:
      motor_moveCCW_cmd_trigger.periodicRun(motor_moveCCW_cmd);
      motor_moveCW_cmd_trigger.periodicRun(motor_moveCW_cmd);
      motor_stop_cmd_trigger.periodicRun(motor_stop_cmd);

      if(motor_moveCCW_cmd_trigger.catchRisingEdge()){
        if(getCCW()){
          queueOutgoing("<IND_CCW, 1>");
        }
        else{
          if (DC_MOTOR_ACTIVATED){
            motorCCW(DC_MOTOR_1_IN1, DC_MOTOR_1_IN2);
            current_DC_motorState_1 = CCW_STATUS;
#if SIMULATION
            sim_startCCW();
#endif
          }
          else{
            eggsTurnerStepperMotor.moveBackward(STEPPER_MOTOR_SPEED_DEFAULT);
          }
        }
      }
      else if(motor_moveCW_cmd_trigger.catchRisingEdge()){
        if(getCW()){
          queueOutgoing("<IND_CW, 1>");
        }
        else{
          if (DC_MOTOR_ACTIVATED){
            motorCW(DC_MOTOR_1_IN1, DC_MOTOR_1_IN2);
            current_DC_motorState_1 = CW_STATUS;
#if SIMULATION
            sim_startCW();
#endif
          }
          else{
            eggsTurnerStepperMotor.moveForward(STEPPER_MOTOR_SPEED_DEFAULT);
          }
        }
      }
      else if(motor_stop_cmd_trigger.catchRisingEdge()){
        if (DC_MOTOR_ACTIVATED){
            motorStop(DC_MOTOR_1_IN1, DC_MOTOR_1_IN2);
          }
          else{
            eggsTurnerStepperMotor.stopMotor();
          }
      }

      /* MECHANICAL SAFETY */
      if(getCCW()
          and
          (
            (
              (!DC_MOTOR_ACTIVATED)
              and
              eggsTurnerStepperMotor.isMovingBackward()
            )
            or
            (
              DC_MOTOR_ACTIVATED
              and
              current_DC_motorState_1 == CCW_STATUS
            )
          )
        ){
        if (DC_MOTOR_ACTIVATED){
          motorStop(DC_MOTOR_1_IN1, DC_MOTOR_1_IN2);
        }
        else{
          eggsTurnerStepperMotor.stopMotor();
        }
      }
      if(getCW()
          and
          (
            (
              (!DC_MOTOR_ACTIVATED)
              and
              eggsTurnerStepperMotor.isMovingForward()
            )
            or
            (
              DC_MOTOR_ACTIVATED
              and
              current_DC_motorState_1 == CW_STATUS
            )
          )
        ){
        if (DC_MOTOR_ACTIVATED){
          motorStop(DC_MOTOR_1_IN1, DC_MOTOR_1_IN2);
        }
        else{
          eggsTurnerStepperMotor.stopMotor();
        }
      }

      break;
    default:
      break;
  }
  /* END STEPPER MOTOR CONTROL SECTION */

  /* MACHINE SINGALING DEVICE - SECTION */
  unsigned long currentTime = millis();
    if (currentTime - lastUpdate >= runInterval) {
        for (int i = 0; i < NUMBER_OF_LIGHTS; i++) updateDevice(lights[i], lightPins[i], 200, 500);
        updateDevice(buzzer, buzzerPin, 100, 300);
        lastUpdate = currentTime;
    }
  /* END MACHINE SINGALING DEVICE - SECTION */


  if(gotTemperatures){
    dtostrf(temperatures[0], 1, 1, fbuffChar);
    queueOutgoing("<TMP01,%s>", fbuffChar);

    dtostrf(temperatures[1], 1, 1, fbuffChar);
    queueOutgoing("<TMP02,%s>", fbuffChar);

    dtostrf(temperatures[2], 1, 1, fbuffChar);
    queueOutgoing("<TMP03,%s>", fbuffChar);

    dtostrf(temperatures[3], 1, 1, fbuffChar);
    queueOutgoing("<TMP04,%s>", fbuffChar);

    dtostrf(humidity_fromDHT22, 1, 1, fbuffChar);
    queueOutgoing("<HUM01,%s>", fbuffChar);

    dtostrf(temp_fromDHT22, 1, 1, fbuffChar);
    queueOutgoing("<HTP01,%s>", fbuffChar);

    dtostrf(temperature_externalTemperatureSensor, 1, 1, fbuffChar);
    queueOutgoing("<EXTT,%s>", fbuffChar);

    dtostrf(waterWeight, 1, 1, fbuffChar);
    queueOutgoing("<WGT01,%s>", fbuffChar);

    /*
       DIAGNOSTICA - si può togliere quando il problema dei silenzi è chiuso.
       UPT = millis() della scheda, MEM = RAM libera. Servono a distinguere tre
       cause che dal PC appaiono identiche (nessun dato per ~2.4 s):
         - uptime che riparte da ~0   -> la scheda si è riavviata
         - uptime continuo ma con un salto di ~2400 ms -> firmware bloccato
         - uptime che avanza regolare  -> la scheda non ha mai smesso di
                                          trasmettere: il problema è lato PC
       MEM basso è normale su un UNO (2 KB totali): qui non indica più
       frammentazione da String (eliminata), resta comunque utile come
       margine generale di sicurezza.
    */
    queueOutgoing("<UPT,%lu>", millis());
    queueOutgoing("<MEM,%d>", freeRam());

    gotTemperatures = false;
  }

  // SENDING TO RPY
  if(listofDataToSend_numberOfData > 0 && serial_communication_is_ok){
    Serial.print('@');
    for(byte i = 0; i < listofDataToSend_numberOfData; i++){
      Serial.print(listofDataToSend[i]);
    }
    Serial.println('#');
  }
  listofDataToSend_numberOfData = 0;

  if (SERIAL_PRINT_CHECK){
    cycle_time = millis() - last_cycle_time;
    last_cycle_time = millis();
    if (cycle_time > 20){
        Serial.println(cycle_time);
    }
  }
  delay(1);

  /* reset command section */
  motor_moveCCW_automatic_var = false;
  motor_moveCW_automatic_var = false;
  motor_stop_automatic_var = false;

  motor_moveCCW_cmd = false;
  motor_moveCW_cmd = false;
  motor_stop_cmd = false;

  if(!ENABLE_HEATER){
    digitalWrite(HEATER_PIN, LOW);
  }

  if(!ENABLE_HUMIDIFIER){
    digitalWrite(HUMIDIFIER_PIN, LOW);
  }

  if(!ENABLE_WATER_ELECTROVALVE){
    digitalWrite(WATER_ELECTROVALVE_PIN, LOW);
  }
}


// ============================================================
//  Inductor read helpers – single call point for sim vs hardware
// ============================================================
bool getCCW() {
#if SIMULATION
  return sim_getCCW();
#else
  return ccw_inductor_input.getInputState();
#endif
}

bool getCW() {
#if SIMULATION
  return sim_getCW();
#else
  return cw_inductor_input.getInputState();
#endif
}


// ============================================================
//  SIMULATION MODE – implementation
// ============================================================
#if SIMULATION

bool sim_getCCW() { return _sim_ccw; }
bool sim_getCW()  { return _sim_cw;  }

void sim_startCCW() {
  _sim_moving  = true;
  _sim_dir_cw  = false;
  _sim_move_t0 = millis();
  _sim_ccw     = false;
  _sim_cw      = false;
}

void sim_startCW() {
  _sim_moving  = true;
  _sim_dir_cw  = true;
  _sim_move_t0 = millis();
  _sim_ccw     = false;
  _sim_cw      = false;
}

// Advance the virtual motor: set the appropriate limit flag when travel time elapses
void sim_motor_tick() {
  if (_sim_moving && (millis() - _sim_move_t0 >= SIM_TRAVEL_MS)) {
    _sim_moving = false;
    if (_sim_dir_cw) { _sim_cw  = true;  _sim_ccw = false; }
    else             { _sim_ccw = true;   _sim_cw  = false; }
  }
}

/*
  sim_generateSensors() – called every SIM_SENSOR_MS from loop().
  Fills all the same global variables that real sensor reads would fill:
    temperatures[0..3]                 – 4 internal DS18B20  (35-39 °C)
    humidity_fromDHT22                 – DHT22 humidity       (50-60 %)
    temp_fromDHT22                     – DHT22 temperature    (~36.5 °C)
    temperature_externalTemperatureSensor – external DS18B20  (~20 °C)
    waterWeight                        – HX711 load cell      (g)
*/
void sim_generateSensors() {
  float t = millis() / 1000.0f;  // seconds since boot

  // ── Internal DS18B20 temperatures ──────────────────────────────────────
  // Centre 37 °C, slow sine ±1 °C (period 60 s), per-sensor placement
  // offset, gaussian-ish noise ±0.1 °C.
  float tBase = 37.0f + sinf(2.0f * PI * t / 60.0f);
  const float kOff[4] = { 0.3f, 0.0f, -0.3f, 0.1f };
  for (int i = 0; i < 4; i++)
    temperatures[i] = tBase + kOff[i] + (random(-10, 11) / 100.0f);

  // ── DHT22 humidity ──────────────────────────────────────────────────────
  // Centre 55 %, ±3 % sine (period 90 s), ±0.3 % noise.
  humidity_fromDHT22 = 55.0f
      + 3.0f * sinf(2.0f * PI * t / 90.0f + 1.0f)
      + (random(-30, 31) / 100.0f);

  // ── DHT22 co-located temperature ───────────────────────────────────────
  // Centre 36.5 °C (slightly lower than DS18B20 due to sensor placement),
  // ±0.5 °C (period 70 s).
  temp_fromDHT22 = 36.5f
      + 0.5f * sinf(2.0f * PI * t / 70.0f + 0.5f)
      + (random(-15, 16) / 100.0f);

  // ── External DS18B20 ───────────────────────────────────────────────────
  // Centre 20 °C, ±2 °C slow drift (period 300 s, simulates ambient
  // day/night variation), ±0.1 °C noise.
  temperature_externalTemperatureSensor = 20.0f
      + 2.0f * sinf(2.0f * PI * t / 300.0f + 2.0f)
      + (random(-10, 11) / 100.0f);

  // ── Water weight (load cell) ────────────────────────────────────────────
  // Slow evaporation: −0.1 g per SIM_SENSOR_MS interval (~12 g/hr).
  // When electrovalve is open: +10 g per interval (~72 g/min fill rate).
  // Clamped to [100 g … 3000 g].
  bool valveOpen = (digitalRead(WATER_ELECTROVALVE_PIN) == HIGH);
  _sim_wgt_g += valveOpen ? 10.0f : -0.1f;
  _sim_wgt_g   = constrain(_sim_wgt_g, 100.0f, 3000.0f);
  waterWeight  = round(_sim_wgt_g * 10.0f) / 10.0f;
}

#endif  // SIMULATION


// ============================================================
//  Utility functions (unchanged from original)
// ============================================================

/*
   RAM libera fra lo heap e lo stack. Il percorso comandi non usa più String
   (vedi commento su MAX_NUMBER_OF_COMMANDS_TO_BOARD), quindi qui non c'è più
   frammentazione da temere; resta comunque utile come diagnostica generale.
*/
int freeRam() {
  extern int __heap_start, *__brkval;
  int v;
  return (int)&v - (__brkval == 0 ? (int)&__heap_start : (int)__brkval);
}

/*
   Accoda un token "<TAG,valore>" in listofDataToSend, format-string in stile
   printf. Sostituisce le vecchie sequenze strcpy/strcat su un buffer globale
   condiviso + assegnazione a String: niente più heap, e vsnprintf tronca da
   solo se il risultato supera OUTGOING_ITEM_LEN invece di scrivere fuori dal
   buffer.
*/
void queueOutgoing(const char *fmt, ...) {
  if (listofDataToSend_numberOfData >= MAX_NUMBER_OF_COMMANDS_TO_BOARD) return;
  va_list args;
  va_start(args, fmt);
  vsnprintf(listofDataToSend[listofDataToSend_numberOfData], OUTGOING_ITEM_LEN, fmt, args);
  va_end(args);
  listofDataToSend_numberOfData++;
}

// Equivalente in-place di String::trim(): toglie spazi/tab/CR/LF iniziali e finali.
void trimInPlace(char *s) {
  char *start = s;
  while (*start == ' ' || *start == '\t' || *start == '\r' || *start == '\n') start++;
  size_t len = strlen(start);
  if (start != s) memmove(s, start, len + 1);
  while (len > 0) {
    char c = s[len - 1];
    if (c != ' ' && c != '\t' && c != '\r' && c != '\n') break;
    s[--len] = '\0';
  }
}

void byteToHex(uint8_t byteValue, char *hexValue) {
  uint8_t highNibble = byteValue >> 4;
  uint8_t lowNibble = byteValue & 0x0F;
  hexValue[0] = highNibble < 10 ? '0' + highNibble : 'A' + (highNibble - 10);
  hexValue[1] = lowNibble < 10 ? '0' + lowNibble : 'A' + (lowNibble - 10);
}

void addressToCharArray(DeviceAddress deviceAddress, char *charArray) {
  for (uint8_t i = 0; i < 8; i++) {
    byteToHex(deviceAddress[i], &charArray[i * 2]);
  }
  charArray[16] = '\0';
}

int readFromBoard(){
  receivingDataFromBoard = true;
  byte rcIndex = 0;
  bool saving = false;
  bool enableReading = false;
  byte receivedCommandsIndex = 0;

  unsigned long  startReceiving = millis();

  while(receivingDataFromBoard){
    if(Serial.available() > 0){
      startReceiving = millis();
      char rc = Serial.read();

      if(rc == '@'){
        enableReading = true;
      }

      if(enableReading){
        if(rc == '<'){
          saving = true;
          rcIndex = 0;
        }
        else if(rc == '>'){
          // Scrive direttamente nel buffer fisso del comando corrente: niente
          // più buffer di appoggio da ricopiare in una String.
          if (receivedCommandsIndex < MAX_NUMBER_OF_COMMANDS_TO_BOARD) {
            receivedCommands[receivedCommandsIndex][rcIndex] = '\0';
            receivedCommandsIndex ++;
          }
          rcIndex = 0;
          saving = false;
        }
        else if(rc == '#'){
          receivingDataFromBoard = false;
        }
        else{
          if(saving && receivedCommandsIndex < MAX_NUMBER_OF_COMMANDS_TO_BOARD
                    && rcIndex < (INCOMING_CMD_LEN - 1)){
            receivedCommands[receivedCommandsIndex][rcIndex] = rc;
            rcIndex ++;
          }
        }
      }
    }
    else{
      if((millis() - startReceiving) > 150){
        receivingDataFromBoard = false;
      }
    }
  }
  return receivedCommandsIndex;
}

void updateDevice(Device &device, int pin, unsigned long fastInterval, unsigned long slowInterval) {
    unsigned long currentTime = millis();
    unsigned long interval = (device.state == FLASH_FAST || device.state == BEEP_FAST) ? fastInterval : slowInterval;

    switch (device.state) {
        case OFF:
            digitalWrite(pin, LOW);
            device.currentState = LOW;
            break;
        case ON:
            digitalWrite(pin, HIGH);
            device.currentState = HIGH;
            break;
        case FLASH_FAST:
        case FLASH_SLOW:
        case BEEP_FAST:
        case BEEP_SLOW:
            if (currentTime - device.lastToggle >= interval) {
                device.currentState = !device.currentState;
                digitalWrite(pin, device.currentState);
                device.lastToggle = currentTime;
            }
            break;
    }
}

bool splitCommand(const char *input,
                   char *tag, size_t tagSize,
                   char *value, size_t valueSize,
                   char *uid, size_t uidSize) {
  const char *firstComma = strchr(input, ',');
  if (!firstComma) return false;
  const char *secondComma = strchr(firstComma + 1, ',');

  size_t tagLen = (size_t)(firstComma - input);
  if (tagLen >= tagSize) tagLen = tagSize - 1;
  strncpy(tag, input, tagLen);
  tag[tagLen] = '\0';

  const char *valueStart = firstComma + 1;
  const char *valueEnd = secondComma ? secondComma : (input + strlen(input));
  size_t valueLen = (size_t)(valueEnd - valueStart);
  if (valueLen >= valueSize) valueLen = valueSize - 1;
  strncpy(value, valueStart, valueLen);
  value[valueLen] = '\0';

  if (secondComma) {
    size_t uidLen = strlen(secondComma + 1);
    if (uidLen >= uidSize) uidLen = uidSize - 1;
    strncpy(uid, secondComma + 1, uidLen);
    uid[uidLen] = '\0';
  } else {
    uid[0] = '\0';
  }

  trimInPlace(tag);
  trimInPlace(value);
  trimInPlace(uid);

  return true;
}

// ============================================================
//  DC motor control functions
// ============================================================

void motorCW(int pin_in1, int pin_in2) {
  analogWrite(pin_in1, MOTOR_SPEED);
  digitalWrite(pin_in2, LOW);
}

void motorCCW(int pin_in1, int pin_in2) {
  digitalWrite(pin_in1, LOW);
  analogWrite(pin_in2, MOTOR_SPEED);
}

void motorStop(int pin_in1, int pin_in2) {
  digitalWrite(pin_in1, LOW);
  digitalWrite(pin_in2, LOW);
}
