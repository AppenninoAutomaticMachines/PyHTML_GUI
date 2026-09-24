#!/bin/bash
export DISPLAY=:0
export XAUTHORITY=/home/filippo/.Xauthority
# exec >> /home/filippo/Documents/PyQT_GUI/debug.log 2>&1  # Decommentare per debug

# Attende finché il display X è disponibile
while ! xset q > /dev/null 2>&1; do
    echo "Attendo che il display grafico sia pronto..."
    sleep 1
done

DELAY=90
BAR_LENGTH=30
GREEN="\e[92m"
YELLOW="\e[93m"
RESET="\e[0m"

echo -e "${YELLOW}Starting application in $DELAY seconds...${RESET}"

for ((i=0; i<=DELAY; i++)); do
    percent=$((100 * i / DELAY))
    filled=$((BAR_LENGTH * i / DELAY))
    empty=$((BAR_LENGTH - filled))
    bar=$(printf "%0.s#" $(seq 1 $filled))
    spaces=$(printf "%0.s-" $(seq 1 $empty))
    remaining=$((DELAY - i))
    time_left=$(printf "%02d:%02d" $((remaining / 60)) $((remaining % 60)))
    printf "\r${GREEN}[%s%s] %3d%% - Time left %s ${RESET}" "$bar" "$spaces" "$percent" "$time_left"
    sleep 1
done

# Attesa porta seriale
SERIAL_PORT="/dev/ttyACM0"
echo -e "\n${YELLOW}Attendo porta seriale $SERIAL_PORT...${RESET}"
for ((i=0; i<30; i++)); do
    [ -e "$SERIAL_PORT" ] && break
    echo "Tentativo $i - porta non ancora pronta..."
    sleep 1
done

if [ ! -e "$SERIAL_PORT" ]; then
    echo -e "${YELLOW}Porta seriale non trovata, avvio comunque...${RESET}"
fi

echo -e "\n${YELLOW}Starting application now!${RESET}"
sleep 3

export DISPLAY=:0
/home/filippo/Documents/PyQT_GUI/venv-py312/bin/python3 -u \
    /home/filippo/Documents/PyQT_GUI/eggsIncubatorMVVM.py

echo -e "\nScript terminato. Premi un tasto per chiudere..."
read -n 1