#ifndef ProfiloLibrary_h
#define ProfiloLibrary_h
#include "Arduino.h"

#define DEFAULT_HYSTERESIS_RANGE 2.5
#define DEFAULT_REFERENCE_TEMPERATURE 37.5

/* TEMPERATURE CONTROLLER CLASS */
class temperatureController{
  public:
    temperatureController();

    void periodicRun(float *temperatures, byte dimension);
    void setTemperatureHysteresis(float lowerTemperature, float higherTemperature);
    void setControlModality(int controlModality);

    bool getOutputState(void);
	float getActualTemperature(void);
	float getMaxTemperature(void);
	float getMeanTemperature(void);
	
    
    // debug
    
    int debug_getHysteresisState(void);

  private:
    // hysteresis parameters
    float _referenceTemperature;
    float _higherTemperature;
    float _lowerTemperature;
	
	float _maxValueTemperature;
	float _meanValueTemperature;

    float _actualTemperature;

    bool _outputState;

    int _controlModality;
    /*
      0: free
      1: controllo basato sulla temperatura più alta
      2: controllo basato sulla media
    */

    int _hysteresisState;


};

/* TIMER CLASS */
class timer{
  public:
    timer();

    void periodicRun(void);
    void reArm(void);
    void enable(void);
    void disable(void);
	void reset(void);

    void setTimeToWait(int timeToWait);
    
    bool getOutputTriggerEdgeType(void);
    bool getOutputTriggerStableType(void);


  private:
    unsigned long _currentTime, _lastTriggerTime;

    int _timeToWait;

    bool _enable;
    bool _outputTrigger_edgeType;
    bool _outputTrigger_stableType;

    bool _resetTimer; // per ripartire a contare
    bool _reArm; // funziona a trigger
	
	bool _first_periodicRun;
	/* tiene traccia della prima chiamata a periodicRun. se passa molto tempo dall'inizializzazione al primo periodicRun rischio di triggerare un edge che non è 
	propriamente voluto. Gli edge si triggerano appena siamo in periodic run. */
};

/* STEPPER MOTOR CLASS */
class stepperMotor{
  public:
    stepperMotor(byte stepPin, byte dirPin, byte MS1, byte MS2, byte MS3, float degreePerStep);

    void periodicRun(void);

    void stopMotor(void);
    void moveForward(float rpm_speed);
    void moveBackward(float rpm_speed);
    void switchPolarity(void);


    //void setMicroSteppingConfiguration(bool MS1, bool MS2, bool MS3);
    float get_actualRPMSpeed(void);
	bool get_stepCommand(void); // funzione che mi dice se è attiva la richiesta di step per il motore o no.
	bool isMovingForward(void);
	bool isMovingBackward(void);

  private:
    byte _stepPin;
    bool _stepCommand; // true = motore deve girare  false = motore fermo

    byte _dirPin;
    bool _dirCommand;
    bool _switchPolarity;

    byte _MS1, _MS2, _MS3; // microstepping configuration

    float _rpm_speed;
    float _degreePerStep;
    float _actual_rpm_speed;
	
	float _conversionVariable_fromRPM_to_msDelay_formula;
	float _conversionVariable_from_msDelay_to_RPM_formula;

    // stepping computation
	unsigned long _stepOnBeginTime;
	
	int _stepOnDuration; //1ms by default, minimo
	
    int _stepDelay;

	byte _steppingState;
    byte _workingState;
};

/* FILTERED INPUT - anti debounce */
class antiDebounceInput{
  public:
    antiDebounceInput(byte pin, int debounceTime);

    void periodicRun(void);
    void changePolarity(void);

    void setDebounceDelay(int debounceDelay);
    bool getInputState(void); // ritorna dicendo se il pulsante è premuto o no
	bool getCurrentInputState(void); // ritorna lo stato attuale del segnale

  private:
    byte _inputPin;
    bool _changePolarity;

    bool _currentInputState;
    bool _previousInputState;

    unsigned long _lastDebounceTime; 
    unsigned long _debounceDelay; 

    bool _inputState_output; // variabile di output di questo oggetto che mi dice lo stato dell'input    
};

/* TRIGGER (per segnali PLC) */
/*
  Oggetto che guarda solo i segnali PLC e si accorge di un falling/rising edge. Va fatto girare periodicamente nell'esecuzione PLC per monitorare 
  il segnale. Ha memoria interna dello stato passato.
  Restituisce sei segnali di tipo EDGE: su un ciclo nel momento in cui accade il falling/rising edge.
  Le uscite edgeType sono azzerate ad ogni chiamata di periodicRun dell'oggetto. 
*/
class trigger{
  public:
    trigger(void);

    void periodicRun(bool signal);

    bool catchRisingEdge(void); // uscita esgeType
    bool catchFallingEdge(void); // uscita esgeType

  private:
    bool _signalState;
    bool _previousSignalState;

    bool _risingEdge;
    bool _fallingEdge;
};

/* TON */
/*
  Per segnali PLC
  Ha in ingresso un segnale. Appena il segnale va a true, inizio a contare il TON. Se va false, il TON si azzera.
  Il TON continua a contare. Appena passo il tempo richeisto di filtraggio msDelay, allora attivo l'uscita _TONOutput, che sta
  su fintantoche l'input sta su. Appena l'input va a false, allora TON si resetta e anche l'uscita si resetta.
  Provvedo anche un'uscita edgeType, che con un edge mi dice appena il TON ha contato.
*/
class TON{
  public:
    TON(int msDelay);

    void periodicRun(bool signal);

    bool getTON_OutputEdgeType(void);
    bool getTON_OutputStableType(void);

    unsigned long getTON_ElapsedTime(void);

  private:
    trigger _localTrigger; // mi dice quando ho il rising edge del segnale

    bool _signalState;

    unsigned long _startCountTimer; // istante di tempo in cui inizio a contare
    unsigned long _elapsedTime;
    int _msDelay; // tempo di filtraggio

    bool _countingActive;

    bool _TON_Output_EdgeType;
    bool _TON_Output_StableType;

};
#endif