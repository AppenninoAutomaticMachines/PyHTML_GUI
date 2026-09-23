#include "Arduino.h"
#include "ProfiloLibrary.h"

/* TEMPERATURE CONTROLLER CLASS */
#define DEVICE_DISCONNECTED_C -127
#define DEFAULT_TEMPERATURE_C 0

temperatureController::temperatureController(){
  _referenceTemperature = DEFAULT_REFERENCE_TEMPERATURE; // 37.5°C
  _higherTemperature = DEFAULT_REFERENCE_TEMPERATURE + DEFAULT_HYSTERESIS_RANGE; // 39.5°C
  _lowerTemperature = DEFAULT_REFERENCE_TEMPERATURE - DEFAULT_HYSTERESIS_RANGE; // 35.5°C

  _outputState = false; // not heating   
  _controlModality = 1; // temperature control based on the HIGHEST value
  _hysteresisState = 0;

}

// aggiungi eventuale costruttore dove gli passi subito il pin che deve controllare del/dei riscaldatori
// in tal caso metto anche due metodini per fare la forzatura ON OFF dell'attuatore, per fare override del comando automatico in caso di necessità.


void temperatureController::periodicRun(float *temperatures, byte dimension){ 
  /* calcoliamo il valore massimo e calcoliamo il valore medio. Ci pensiamo poi dopo a quale valore dare fuori, dipendentemente dalla modalità di funzionamento.
    Li calcolo sempre perché comuqnue possono far sempre comodo. Vedi, infatti, il calcolo dell'umidità, che usa il valore medio. 
  */ 
  if(dimension > 0){
    // MEAN VALUE
    float sum = 0.0;
    byte goodTemperaturesCounter = 0;		

    // MAX VALUE
    float maxTemperature = temperatures[0];

    for (int i = 0; i < dimension; i++){
      float tempC = temperatures[i];

      if (tempC != DEVICE_DISCONNECTED_C){        
        // MEAN VALUE COMPUTATION
        sum += tempC;
        goodTemperaturesCounter++;

        // MAX VALUE COMPUTATION
        if(tempC > maxTemperature){
          maxTemperature = tempC;
        }
      } 
      else{
        // Handle the case when a temperature reading is invalid
        // This could include fault handling or logging
      }
    }

    // Check if there are valid temperatures before computing the mean
    if (goodTemperaturesCounter > 0){
      _meanValueTemperature = sum / goodTemperaturesCounter;
    } 
    else{
      // Handle the case when no valid temperatures are available
      // This could include setting _actualTemperature to a default value
      _meanValueTemperature = DEFAULT_TEMPERATURE_C;
    }

    _maxValueTemperature = maxTemperature;
  }
  else{
    // Handle the case when the array is empty
    // Set _actualTemperature to a default value or handle it accordingly
    _maxValueTemperature = DEFAULT_TEMPERATURE_C;
    _meanValueTemperature = DEFAULT_TEMPERATURE_C;
  }

    

  switch(_controlModality){ // qui controlliamo l'output
    case 0: 
      break;

    case 1:
      _actualTemperature = _maxValueTemperature;
      break;

    case 2: 
      _actualTemperature = _meanValueTemperature;
		break;

    default:
      break;
  }
  // da qui ho la _actualTemperature

  _outputState = false; // not heating  
  switch(_hysteresisState){ // qui controlliamo l'output
    case 0: // primo stato, appena accendo. Dobbiamo definire dove siamo nel ciclo di isteresi.
      if(_actualTemperature < _lowerTemperature){
        _hysteresisState = 1;
      }
      else if(_actualTemperature > _higherTemperature){
        _hysteresisState = 2;              
      }
      else{ // se sono nel mezzo dell'intervallo, NON heating tratto sotto
        _hysteresisState = 2;
      }
      break;


    case 1: // tratto sopra di heating
      _outputState = true; //heating 

      if(_actualTemperature > _higherTemperature){
        _hysteresisState = 2;     
      }
      break;

    case 2: // tratto sotto di not heating
      _outputState = false; //not heating 
        
      if(_actualTemperature < _lowerTemperature){
        _hysteresisState = 1;     
      }
      break;

    default:
      break;
  }

  /* TIMING COUNT: conteggio di quanto sta ON l'attuatore. */
  
}

void temperatureController::setTemperatureHysteresis(float lowerTemperature, float higherTemperature){
  _higherTemperature = higherTemperature;
  _lowerTemperature = lowerTemperature;
}

void temperatureController::setControlModality(int controlModality){
  _controlModality = controlModality;
}

bool temperatureController::getOutputState(void){
  return _outputState;
}

float temperatureController::getActualTemperature(void){
  return _actualTemperature;
}

float temperatureController::getMaxTemperature(void){
  return _maxValueTemperature;
}

float temperatureController::getMeanTemperature(void){
  return _meanValueTemperature;
}

int temperatureController::debug_getHysteresisState(void){
  return _hysteresisState;
}


/* TIMER CLASS */
#define DEFAULT_WAITING_TIME 1000 //ms

timer::timer(){
  _currentTime = millis();
  _lastTriggerTime = millis();

  _timeToWait = DEFAULT_WAITING_TIME; 
  _enable = true; // di default parto con timer abilitato
  _outputTrigger_edgeType = false;
  _outputTrigger_stableType = false;

  _resetTimer = false;
  _reArm = false;
  
  _first_periodicRun = true;
}

void timer::periodicRun(void){
  if(_first_periodicRun){
    // aggiorniamo il _lastTriggerTime per la prima volta
    _lastTriggerTime = millis();
    _first_periodicRun = false;
  }
  
  if(_enable){
    _outputTrigger_edgeType = false;
	
	if(_resetTimer){
        _lastTriggerTime = _currentTime;
		_outputTrigger_stableType = false;
		_outputTrigger_edgeType = false;
        _resetTimer = false;
      }


    if(!_outputTrigger_stableType){
      /*
        Fintanto che non ho dato il trigger di output faccio contare il timer. Appena l'ho dato, ho bisogno che ci sia un qualcuno che esternamente
        ri-armi il trigger così che possa tornare a contare. Dall'esterno qualcuno mette reArm = true

        Ricorda che ci sono le due uscite del timer. Una che sta su finché non riparto a contare, una che funziona ad edge e sta su per un ciclo PLC.
      */
      _currentTime = millis();

      
      _outputTrigger_stableType = false;

      if((_currentTime - _lastTriggerTime) >= _timeToWait){
        _lastTriggerTime = _currentTime; // let's remember the last time the time has triggered its output

        _outputTrigger_edgeType = true;
        _outputTrigger_stableType = true;
      }
    }
    else{ 
      /*
        Questa è la fase di ri-armo del trigger.
        Mi salvo il tempo attuale e inizio a contare di nuovo. Metto anche a false il bit di riarmo
      */  
      if(_reArm){ // appena ho il comando di riarmo, inizio a contare di nuovo.
        _lastTriggerTime = millis();
        _outputTrigger_stableType = false;
        _reArm = false;
      }
    }      
  }
  else{
    _lastTriggerTime = millis();
    _outputTrigger_stableType = false;
    _reArm = false;
  }
} 

void timer::setTimeToWait(int timeToWait){
  _timeToWait = timeToWait;
}

void timer::reArm(void){
  _reArm = true;
}

void timer::enable(void){
  _enable = true;
}

void timer::disable(void){
  _enable = false;
}

void timer::reset(void){
  _resetTimer = true;
}

bool timer::getOutputTriggerEdgeType(void){
  return _outputTrigger_edgeType;
}

bool timer::getOutputTriggerStableType(void){
  return _outputTrigger_stableType;
}

/* STEPPER MOTOR CLASS */

stepperMotor::stepperMotor(byte stepPin, byte dirPin, byte MS1, byte MS2, byte MS3, float degreePerStep){
  _stepPin = stepPin;
  _stepCommand = false;

  _dirPin = dirPin;
  _dirCommand = false;
  _switchPolarity = false;

  _MS1 = MS1;
  _MS2 = MS2;
  _MS3 = MS3;

  _rpm_speed = 1; // default 1rpm
  _degreePerStep = degreePerStep;

  _steppingState = 0;
  _workingState = 0; // STOP
  
  _stepOnDuration = 1; //1ms by default, durata minima dello step on
  
  _conversionVariable_fromRPM_to_msDelay_formula = _degreePerStep / 360.0 * 60 * 1000;
  _conversionVariable_from_msDelay_to_RPM_formula = _degreePerStep * 1000 * 60 / 360;
}

void stepperMotor::periodicRun(void){

  /* macchina a stati di gestione dei comandi */
  switch(_workingState){
    case 0: // STOP
      _stepCommand = false;
      break;

    case 1:// MOVE FORWARD
      _stepCommand = true;
      _dirCommand = true;
      break;

    case 2:// MOVE BACKWARD
      _stepCommand = true;
      _dirCommand = false;
      break;

    default:
      break;
  }


  if(_stepCommand){
    // scelgo direzione e abilito i timer
    if(_switchPolarity){
      if(_dirCommand){
        digitalWrite(_dirPin, LOW);
      }
      else{
        digitalWrite(_dirPin, HIGH);
      }
    }
    else{
      if(_dirCommand){
        digitalWrite(_dirPin, HIGH);
      }
      else{
        digitalWrite(_dirPin, LOW);
      }
    }      
    
    _stepDelay = _conversionVariable_fromRPM_to_msDelay_formula / _rpm_speed; //ms, cast to int type
    // from RPM to °/ms --> (rpm * 360) / (60 * 1000)   [°/ms]
    // compute the ms you need to wait btw to degreePerStep steps: degreePerStep / stepDelay [ms]
    _actual_rpm_speed = _conversionVariable_from_msDelay_to_RPM_formula / _stepDelay;

    // intervallo minimo fra due step è 2ms (due fronti positivi del segnale allo step).
    // perché 1ms lo uso per tenere stabile il segnale alto.
    if(_stepDelay < 2){ 
      _stepDelay = 2;
    }
  }  

  // se non ho più richiesta di step, allora torno in stato di attesa.
  if(!_stepCommand){
    digitalWrite(_stepPin, LOW);
    _steppingState = 0;
  }

  switch(_steppingState){
    case 0: // attendo il comando di step
      if(_stepCommand){
        _stepOnBeginTime = millis(); // mi ricordo dell'istante di tempo in cui dò lo step
        digitalWrite(_stepPin, HIGH);
        _steppingState = 1;
      }
      break;
    case 1: // attendo 1ms step ON
      if(millis() - _stepOnBeginTime > _stepOnDuration){
        digitalWrite(_stepPin, LOW); // spengo pin, ho generato lo step
        _steppingState = 2;
      }
      break;
    case 2: // ora attendo il passaggio del tempo necessario per dare il prossimo step
      if(millis() - _stepOnBeginTime > _stepDelay){
         _stepOnBeginTime = millis();
         digitalWrite(_stepPin, HIGH); // spengo pin, ho generato lo step
        _steppingState = 1;
      }
      break;
    default:
      break;
  }
}

void stepperMotor::stopMotor(void){
  _workingState = 0;
}

void stepperMotor::moveForward(float rpm_speed){
  _rpm_speed = rpm_speed;
  _workingState = 1;
}

void stepperMotor::moveBackward(float rpm_speed){
  _rpm_speed = rpm_speed;
  _workingState = 2;
}

void stepperMotor::switchPolarity(void){
  // chiami questo metodo per cambiare il senso di rotazione del motore, per compensare il montaggio.
  _switchPolarity = true;
}

float stepperMotor::get_actualRPMSpeed(void){
  return _actual_rpm_speed;
}

bool stepperMotor::get_stepCommand(void){
  return _stepCommand;
}

bool stepperMotor::isMovingForward(void){
  if(_workingState == 1){
    return true;
  }
  else{
    return false;
  }
}

bool stepperMotor::isMovingBackward(void){
  if(_workingState == 2){
    return true;
  }
  else{
    return false;
  }
}


/* FILTERED INPUT - anti debounce */
antiDebounceInput::antiDebounceInput(byte pin, int debounceDelay){
  _inputPin = pin;  
  _changePolarity = false;
  _previousInputState = false;

  _lastDebounceTime = 0;
  _debounceDelay = debounceDelay;

  _inputState_output = false;
  }

void antiDebounceInput::periodicRun(void){
  if(_changePolarity){
    _currentInputState = !digitalRead(_inputPin);
  }
  else{
    _currentInputState = digitalRead(_inputPin);
  }

  if(_currentInputState != _previousInputState){
    _lastDebounceTime = millis();
  }

  if((millis() - _lastDebounceTime) > _debounceDelay){
    if (_currentInputState != _inputState_output) {
      _inputState_output = _currentInputState;
    }
  }
  
  _previousInputState = _currentInputState;
}

void antiDebounceInput::changePolarity(void){
  _changePolarity = true;
}

void antiDebounceInput::setDebounceDelay(int debounceDelay){
  _debounceDelay = debounceDelay;
}

bool antiDebounceInput::getInputState(void){
  return _inputState_output;  
}

bool antiDebounceInput::getCurrentInputState(void){
	return _currentInputState;
}

/* TRIGGER (per segnali PLC) */
trigger::trigger(void){
  _signalState = false;
  _previousSignalState = false;

  _risingEdge = false;
  _fallingEdge = false;
}

void trigger::periodicRun(bool signal){
  _risingEdge = false; // ad ogni ciclo vanno azzerati.
  _fallingEdge = false;

  _signalState = signal;

  if(_signalState != _previousSignalState){
    if(_signalState){ // RISING EDGE
      /* se ora vale true ed è diverso da prima, significa che siamo in rising edge */
      _risingEdge = true;
    }
    else{
      /* se ora vale false ed è diverso da prima, significa che siamo in falling edge */
      _fallingEdge = true;
    }
  }
  _previousSignalState = _signalState;
}

bool trigger::catchRisingEdge(void){
  return _risingEdge;
}

bool trigger::catchFallingEdge(void){
  return _fallingEdge;
}

/* TIMER */
TON::TON(int msDelay){
  _signalState = false;

  _startCountTimer = 0;
  _elapsedTime = 0;

  _msDelay = msDelay;

  _countingActive = false;

  _TON_Output_EdgeType = false;
  _TON_Output_StableType = false;
}

void TON::periodicRun(bool signal){
  _signalState = signal;
  _TON_Output_EdgeType = false;

  _localTrigger.periodicRun(_signalState);

  if(_localTrigger.catchRisingEdge()){ // appena vedo RISING EDGE, allora inizio a contare il tempo.
    _startCountTimer = millis();
    _countingActive = true;
  }

  if(_countingActive){
    _elapsedTime = millis() - _startCountTimer;

    if(_elapsedTime >= _msDelay){
      _TON_Output_EdgeType = true;
      _TON_Output_StableType = true;

      _countingActive = false;
    }
  }

  if(_localTrigger.catchFallingEdge()){ // appena vedo FALLING EDGE, allora reset di tutto.
    _elapsedTime = 0;
    _countingActive = false;
    _TON_Output_StableType = false;
  }
}

bool TON::getTON_OutputEdgeType(void){
  return _TON_Output_EdgeType;
}

bool TON::getTON_OutputStableType(void){
  return _TON_Output_StableType;
}

unsigned long TON::getTON_ElapsedTime(void){
  return _elapsedTime;
}