"""
ESP32 Vindueskontrol til automatisk vindues-styring.

Denne MicroPython kode implementerer:
- Stepper motor kontrol til vindues åbning/lukning
- Solenoid låsemekanisme - sikring mod indbrud
- Buzzer advarelseslyde før vindues bevægelse
- MQTT kommando interface fra RPi5
- Kontrol over position og statusopdatering

Hardware:
- ESP32 DevKitc V4
- 28BYJ-48 Stepper motor + ULN2003 driver
- 5V Solenoid
- 3.3V aktiv buzzer

MQTT Kommandoer:
- 'aaben': Fuld åbning af vindue
- 'luk': Fuld lukning af vindue
- 'kort_aaben': 5 minutters udluftning derefter auto-luk i minimum 15 minutter
- 'manuel_aaben': Manuel åbning (1/5 af max ad gangen)
- 'manuel_luk': Manuel lukning (1/5 af max ad gangen)

Position Tracking:
Holder styr på nuværende position i steps fra lukket (0) til
fuldt åben (STEPS_HELT_ÅBEN). Hvis vi ønskede altid at kende den
absolutte position, ville vi kunne benytte et potentiometer på den
modsatte side af vinduet, som havde styr på en "globale" postion.

Safety Features:
- Buzzer advarsel før bevægelse
- Solenoid lås mod indbrud
- Ved fejl slukkes motor og solenoid
- Status reportering efter hver kommando

Note:
STEPS_HELT_ÅBEN skal kalibreres efter 3D print færdiggørelse.
"""

from machine import Pin, PWM
from time import sleep, time
import network
from umqtt.simple import MQTTClient
import json


# Konfiguration af MQTT

MQTT_SERVER = '192.168.4.1'
"""MQTT broker IP adresse (RPi5 AP)."""

ENHEDS_ID = 'esp32_vindue'
"""Unik identifier til MQTT broker."""

MQTT_TOPIC_COMMAND = 'vindue/kommando'
"""MQTT topic til at modtage vindueskommandoer."""

MQTT_TOPIC_STATUS = 'vindue/status'
"""MQTT topic til at sende vinduesstatus."""


# Konfiguration af WiFi

SSID = 'RaspberryPi_AP'
"""WiFi SSID (netværksnavn)."""

PASSWORD = 'RPI12345'
"""WiFi adgangskode."""

WIFI_TIMEOUT = 20
"""Max sekunder at vente på WiFi forbindelse."""


# Konfiguration af hardware pins

STEPPER_PIN1 = 16
"""GPIO pin til stepper motor coil 1 (via ULN2003)."""

STEPPER_PIN2 = 17
"""GPIO pin til stepper motor coil 2 (via ULN2003)."""

STEPPER_PIN3 = 5
"""GPIO pin til stepper motor coil 3 (via ULN2003)."""

STEPPER_PIN4 = 18
"""GPIO pin til stepper motor coil 4 (via ULN2003)."""

SOLENOID_PIN = 22
"""GPIO pin til MOSFET der styrer solenoiden."""

BUZZER_PIN = 23
"""GPIO pin til buzzer"""


# Konfiguration af stepper motor

STEPS_PR_SEKUND = 200
"""Stepper motor hastighed i steps per sekund."""

DELAY_MELLEM_STEPS = 1.0 / STEPS_PR_SEKUND
"""Beregnet delay mellem hver step i sekunder."""

STEPS_HELT_ÅBEN = 50
"""
Total steps fra lukket til fuldt åben position.

Skal kalibreres:
Dette er en placeholder værdi. Efter 3D print færdiggørelse:
"""


# Konfiguration af stepper motor step sekvens

STEP_SEKVENS = [
    [1, 0, 0, 0],
    [1, 1, 0, 0],
    [0, 1, 0, 0],
    [0, 1, 1, 0],
    [0, 0, 1, 0],
    [0, 0, 1, 1],
    [0, 0, 0, 1],
    [1, 0, 0, 1]
]
"""
Half-step sekvens til 28BYJ-48 stepper motor.
Half step giver en mere smooth åbning og lukning
"""

# Konfiguration af solenoid

SOLENOID_AKTIVERING_DELAY = 1
"""Sekunder at vente efter solenoid aktivering før motoren starter."""


# Konfiguration af buzzer

BUZZER_PWM_DUTY = 150
"""PWM duty cycle til buzzer 512 er højest."""

BUZZER_TONE_ÅBNER = [
    (262, 0.45, 0.10),
    (392, 0.45, 0.10)
]
"""
Buzzer sekvens til åbnings-advarsel.
Format: (frekvens, tone_afspilningstid, stille_efter_tone)
"""

BUZZER_TONE_LUKKER = [
    (392, 0.45, 0.10),
    (262, 0.45, 0.10)
]
"""Buzzer sekvens til luknings-advarsel."""


# Konfiguration af kort åbning timer

KORT_ÅBNING_VARIGHED = 300
"""
Sekunder vindue forbliver åbent ved 'kort_aaben' kommando (5 minutter).

Bruges til hurtig udluftning når udendørs vejr er dårligt.
Efter timeout lukkes vindue automatisk.
"""

MQTT_CHECK_INTERVAL = 0.1
"""Sekunder mellem MQTT message-checks under kort åbnings-timeren."""


# Konfiguration af manuel kontrol

MANUEL_STEP_FRAKTION = 5
"""
Manuel kontrol bevæger vinduet i 1/5-grads bevægelse.

manuel_aaben og manuel_luk flytter vindue STEPS_HELT_ÅBEN / 5 steps
per kommando. Dette giver præcis kontrol fra touchscreen.
"""

# Global state

nuværende_position = 0
"""
Nuværende vindues position i steps.

Vi sætter værdien til 0 som svarer til lukket. STEPS_HELT_ÅBEN er fuldt åben

Note: Denne værdi nulstilles ved ESP32 reboot. Vi antager
at vindue er lukket ved opstart. Ville kræve et potentiometer
for at sikre at vi altid kendte den præcise position
"""

vindue_status = "lukket"
"""
Nuværende vinduesstatus.

Værdier:
- "lukket": Position = 0
- "aaben": Position = STEPS_HELT_ÅBEN
"""


# Hardware setup funktioner

def opsæt_hardware():
    """
    Initialiserer alle vores hardware komponenter.
    
    Setup:
        - 4x GPIO pins til stepper motor (OUTPUT)
        - 1x GPIO pin til solenoid (MOSFET) (OUTPUT)
        - 1x PWM pin til buzzer (OUTPUT)
    
    Returns:
        Tuple med (stepper_pins, solenoid, buzzer)
    
    Note:
        Solenoid starter low, for at holde vinduet
        låst som standard.
    """
    
    # Stepper motor pins
    stepper_pins = [
        Pin(STEPPER_PIN1, Pin.OUT),
        Pin(STEPPER_PIN2, Pin.OUT),
        Pin(STEPPER_PIN3, Pin.OUT),
        Pin(STEPPER_PIN4, Pin.OUT)
    ]
    
    # Solenoid MOSFET gpio pin
    solenoid = Pin(SOLENOID_PIN, Pin.OUT)
    solenoid.value(0)
    
    # Buzzer PWM pin (starter med duty 0 for at være stille)
    buzzer = PWM(Pin(BUZZER_PIN), duty=0)
    
    return stepper_pins, solenoid, buzzer


# Stepper motor funktioner

def sluk_stepper_motor(stepper_pins):
    """
    Slukker alle stepper motor coils.
    
    Power saving:
        Når motor ikke kører, sluk coils for at spare strøm og
        reducere motor opvarmning.
    
    Note:
        Ikke testet om det faktisk virker
    """
    for pin in stepper_pins:
        pin.value(0)


def kør_steps(stepper_pins, steps, retning=1):
    """
    Kører stepper motoren i et specificeret antal steps i en given retning.
    
    Args:
        stepper_pins: Liste af Pin objekter
        steps: Antal steps at køre (positive integer)
        retning: 1 for åbning, -1 for lukning
    
    Global State:
        Opdaterer nuværende_position baseret på antal steps og retning.
        Sørger for vores position til er i vores range [0, STEPS_HELT_ÅBEN].
    
    Note:
        Position tracking er relativ - ikke globalt.
        Ved fejl/afbrydelse kan position blive misvisende.
    """
    global nuværende_position
    
    step_count = len(STEP_SEKVENS)
    
    # Kør det specificerede antal steps
    for _ in range(abs(steps)):
        # Gennemløb step sekvens
        for i in range(step_count):
            # Beregn step index baseret på retning
            if retning > 0:
                # fremad (åbning)
                step_index = i
            else:
                # baglæns (lukning)
                step_index = step_count - 1 - i
            
            # Aktiver motor coils iht. step sekvens
            for pin_idx, pin in enumerate(stepper_pins):
                pin.value(STEP_SEKVENS[step_index][pin_idx])
            
            # Delay mellem steps (hastighed)
            sleep(DELAY_MELLEM_STEPS)
    
    # Opdater position efter bevægelse
    nuværende_position += steps * retning
    
    # Sørg for at vi ikke får et tal der bryder vores range
    nuværende_position = max(0, min(nuværende_position, STEPS_HELT_ÅBEN))


# Solenoid funktioner

def aktiver_solenoid(solenoid):
    """
    Aktiverer solenoid - låser op.
    
    Note:
        Solenoid skal være aktiveret før stepper-motoren
        starter for at undgå mekanisk sammenstød.
    """
    solenoid.value(1)


def deaktiver_solenoid(solenoid):
    """
    Deaktiverer solenoid - låses.
    
    Note:
        Solenoid skal låse vinduet efter stepper-motoren stopper for
        at fastholde positionen.
    """
    solenoid.value(0)


# Buzzer funktioner

def buzzer_tone(buzzer, frekvens, tone_afspilningstid, stille_efter_tone):
    """
    Afspiller en tone på buzzer med specificeret frekvens og afspilningstid.
    
    Args:
        buzzer: PWM objekt
        frekvens: Tonefrekvens i Hz
        tone_afspilningstid: Længde af tone i sekunder
        stille__efter_tone: Pause efter tone i sekunder
    """
    # Aktiver buzzer med tone
    buzzer.duty(BUZZER_PWM_DUTY)
    buzzer.freq(frekvens)
    sleep(tone_afspilningstid)
    
    # Deaktiver buzzer (stille)
    buzzer.duty(0)
    sleep(stille_efter_tone)


def afspil_warning_åbner(buzzer):
    """Afspiller to advarselstoner før vindues åbning."""
    for frekvens, tone_afspilningstid, stille_efter_tone in BUZZER_TONE_ÅBNER:
        buzzer_tone(buzzer, frekvens, tone_afspilningstid, stille_efter_tone)


def afspil_warning_lukker(buzzer):
    """Afspiller 2 advarsels toner før vinduets lukning."""
    for frekvens, tone_afspilningstid, stille_efter_tone in BUZZER_TONE_LUKKER:
        buzzer_tone(buzzer, frekvens, tone_afspilningstid, stille_efter_tone)


def stop_buzzer(buzzer):
    """Stopper buzzeren

    Note:
        Bruges ved fejl for at undgå at buzzeren larmer efter et crash.
    """
    buzzer.duty(0)


# Vindues kontrol funktioner

def åben_vindue(stepper_pins, solenoid, buzzer):
    """
    Åbner vindue fuldt fra nuværende position.
    
    Sequence:
        1. Tjek om vinduet allerede er fuldt åben
        2. Afspil advarselsbuzzer
        3. Aktiver solenoid
        4. Vent på SOLENOID_AKTIVERING_DELAY
        5. Kør stepper-motor (i åben retningen)
        6. Deaktiver solenoid
        7. Sluk motor
        8. Opdater status
    
    Raises:
        Exception: Ved fejl under åbning
    """
    global vindue_status, nuværende_position
    
    # kort return hvis allerede fuldt åben
    if nuværende_position >= STEPS_HELT_ÅBEN:
        print("Vinduet er allerede fuldt åbent")
        return
    
    
    try:
        # Debug
        print("Åbner vindue")
        
        # Warning buzzer
        afspil_warning_åbner(buzzer)
        
        # oplås vindue
        aktiver_solenoid(solenoid)
        sleep(SOLENOID_AKTIVERING_DELAY)
        
        # Beregn steps at køre
        steps_at_køre = STEPS_HELT_ÅBEN - nuværende_position
        print("Kører {} steps".format(steps_at_køre))
        
        # Kør motor
        kør_steps(stepper_pins, steps_at_køre, retning=1)
        
        # lås vinduet
        deaktiver_solenoid(solenoid)
        
        # Sluk motor
        sluk_stepper_motor(stepper_pins)
        
        # Opdater status
        vindue_status = "aaben"
        print("Vindue åbnet (position: {})".format(nuværende_position))
        
    except Exception as fejl:
        # Stop ved fejl
        print("Fejl under åbning: {}".format(fejl))
        deaktiver_solenoid(solenoid)
        sluk_stepper_motor(stepper_pins)
        stop_buzzer(buzzer)
        raise


def kort_åben_vindue(stepper_pins, solenoid, buzzer, client):
    """
    Åbner vindue i 5 minutter derefter automatisk lukning.
    
    Sekvens:
        1. Åbn vindue fuldt
        2. Send status update
        3. Start 5 minutters timer
        4. Hent MQTT beskeder hver 100ms
        5. Ved timeout: Luk vindue automatisk
        6. Ved manuel luk kommando: Afbryd timer
    
    Use Case:
        Bruges når udendørs vejr er dårligt men udluftning stadig
        nødvendigt. 5 minutter er nok til, at få lidt rent luft
        ind uden for store gener.
    
    Note:
        Denne funktion blokerer alt andet end MQTT i 5 minutter.
        MQTT Kommandoer kan stadig modtages via check_msg() poll.
    """
    global vindue_status, nuværende_position
    
    try:
        print("Kort åbning - {}sekunders timer".format(KORT_ÅBNING_VARIGHED))
        
        # Åbn vindue fuldt
        afspil_warning_åbner(buzzer)
        aktiver_solenoid(solenoid)
        sleep(SOLENOID_AKTIVERING_DELAY)
        
        if nuværende_position < STEPS_HELT_ÅBEN:
            steps_at_køre = STEPS_HELT_ÅBEN - nuværende_position
            kør_steps(stepper_pins, steps_at_køre, retning=1)
        
        deaktiver_solenoid(solenoid)
        sluk_stepper_motor(stepper_pins)
        vindue_status = "aaben"
        
        # Send status opdatering
        send_status(client)
        print("Vindue åbnet - venter {}sekunder".format(KORT_ÅBNING_VARIGHED))
        
        # Start timer
        start_tid = time()
        
        # Poll loop
        while (time() - start_tid) < KORT_ÅBNING_VARIGHED:
            # Tjek for MQTT beskeder (manuel luk kommando)
            client.check_msg()
            
            # Tjek om vindue blev lukket manuelt
            if vindue_status != "aaben":
                print("Timer afbrudt - vindue lukkes manuelt")
                return
            
            # Sleep mellem polls
            sleep(MQTT_CHECK_INTERVAL)
        
        # Timer udløbet - automatisk lukning
        print("Timer udløbet - lukker automatisk")
        luk_vindue(stepper_pins, solenoid, buzzer)
        send_status(client)
        
    except Exception as fejl:
        print("Fejl under kort åbning: {}".format(fejl))
        deaktiver_solenoid(solenoid)
        sluk_stepper_motor(stepper_pins)
        stop_buzzer(buzzer)
        raise


def luk_vindue(stepper_pins, solenoid, buzzer):
    """
    Lukker vindue fuldt fra nuværende position.
    
    Sequence:
        1. Tjek om det allerede er lukket
        2. Afspil advarselsbuzzer
        3. Aktiver solenoid
        4. Vent på SOLENOID_AKTIVERING_DELAY
        5. Kør stepper-motor (luk retning)
        6. Deaktiver solenoid
        7. Sluk motor
        8. Opdater status
    
    Raises:
        Exception: Ved fejl under lukning
    """
    global vindue_status, nuværende_position
    
    # kort return hvis vinduet allerede er lukket
    if nuværende_position <= 0:
        print("Vinduet er allerede lukket")
        return
    
    try:
        print("Lukker vindue")
        
        # Advarselsbuzzer
        afspil_warning_lukker(buzzer)
        
        # Aktiver solenoiden så den ikke støder på
        aktiver_solenoid(solenoid)
        sleep(SOLENOID_AKTIVERING_DELAY)
        
        # Beregn steps at køre
        steps_at_køre = nuværende_position
        print("Kører {} steps".format(steps_at_køre))
        
        # Kør motor (baglæns retning)
        kør_steps(stepper_pins, steps_at_køre, retning=-1)
        
        # Lås vinduet
        deaktiver_solenoid(solenoid)
        
        # Sluk motor
        sluk_stepper_motor(stepper_pins)
        
        # Opdater status
        vindue_status = "lukket"
        print("Vindue lukket (position: {})".format(nuværende_position))
        
    except Exception as fejl:
        print("Fejl under lukning: {}".format(fejl))
        deaktiver_solenoid(solenoid)
        sluk_stepper_motor(stepper_pins)
        stop_buzzer(buzzer)
        raise


def manuel_åben(stepper_pins, solenoid):
    """
    Manuel åbning - flytter vindue 1/5 af total range.
    
    Use Case:
        Touchscreen skærmen tillader gradvis åbning ved at trykke
        manuel_aaben knap flere gange. Giver præcis kontrol.
    
    Inkrement:
        STEPS_HELT_ÅBEN / MANUEL_STEP_FRAKTION steps per kald
        Default: 1/5 = 20% af total range
    
    Note:
        Ingen buzzer warning da manuel kontrol forventer brugerens opmærksomhed.
    """
    global nuværende_position, vindue_status
    
    # kort return hvis allerede fuldt åben
    if nuværende_position >= STEPS_HELT_ÅBEN:
        print("Vindue allerede fuldt åbent")
        return
    
    try:
        print("Manuel åbning (1/{})".format(MANUEL_STEP_FRAKTION))
        
        # oplås vinduet
        aktiver_solenoid(solenoid)
        
        # Beregn inkrement steps
        steps = STEPS_HELT_ÅBEN // MANUEL_STEP_FRAKTION
        
        # Kør motor
        kør_steps(stepper_pins, steps, retning=1)
        
        # deaktiver solenoid
        deaktiver_solenoid(solenoid)
        
        # Sluk motor
        sluk_stepper_motor(stepper_pins)

        # Da det ikke er lukket længere må vinduet være åbent
        vindue_status = "aaben"
        
        # Opdater status hvis fuldt åben
        if nuværende_position >= STEPS_HELT_ÅBEN:
            vindue_status = "aaben"
            print("Vindue nu fuldt åbent")
        else:
            print("Position: {}/{}".format(nuværende_position, STEPS_HELT_ÅBEN))
        
    except Exception as fejl:
        print("Fejl under manuel åbning: {}".format(fejl))
        deaktiver_solenoid(solenoid)
        sluk_stepper_motor(stepper_pins)
        raise


def manuel_luk(stepper_pins, solenoid):
    """
    Manuel lukning - flytter vindue 1/5 af total range.
    
    Use Case:
        Touchscreen skærmen tillader gradvis lukning ved at trykke
        manuel_luk knappen.
    
    Increment:
        STEPS_HELT_ÅBEN / MANUEL_STEP_FRAKTION steps per kald
        Default: 1/5 = 20% af total range
    """
    global nuværende_position, vindue_status
    
    # Kort return hvis allerede lukket
    if nuværende_position <= 0:
        print("Vindue allerede lukket")
        return
    
    try:
        print("Manuel lukning (1/{})".format(MANUEL_STEP_FRAKTION))
        
        # Åben vindue
        aktiver_solenoid(solenoid)
        
        # Beregn inkrement steps
        steps = STEPS_HELT_ÅBEN // MANUEL_STEP_FRAKTION
        
        # Kør motor (baglæns)
        kør_steps(stepper_pins, steps, retning=-1)
        
        # Deaktiver solenoid
        deaktiver_solenoid(solenoid)
        
        # Sluk motor
        sluk_stepper_motor(stepper_pins)
        
        # Opdater status hvis fuldt lukket
        if nuværende_position <= 0:
            vindue_status = "lukket"
            print("Vindue nu fuldt lukket")
        else:
            vindue_status = "aaben"
            print("Position: {}/{}".format(nuværende_position, STEPS_HELT_ÅBEN))
        
    except Exception as fejl:
        print("Fejl under manuel lukning: {}".format(fejl))
        deaktiver_solenoid(solenoid)
        sluk_stepper_motor(stepper_pins)
        raise


# WiFi funktioner

def forbind_wifi():
    """
    Forbinder til WiFi Access Point med timeout.
    
    Aktiverer WiFi station mode og forbinder til konfigureret SSID.
    Station mode, er hvor at ESP32'eren er forbundet til et WiFi AP
    og ikke opretter sit eget.

    Returns:
        WLAN objekt hvis forbindelse succesfuld
    
    Raises:
        Exception: Hvis WiFi forbindelse fejler efter timeout
    """
    print("Forbinder til WiFi: {}".format(SSID))
    
    # Aktiver WiFi station mode
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    
    # Tjek om den allerede er forbundet
    if wlan.isconnected():
        print("WiFi allerede forbundet")
        print("IP adresse: {}".format(wlan.ifconfig()[0]))
        return wlan
    
    # Start forsøg på at forbinde til WiFi AP
    wlan.connect(SSID, PASSWORD)
    
    # Vent på forbindelse med timeout
    timeout = WIFI_TIMEOUT
    while not wlan.isconnected() and timeout > 0:
        sleep(1)
        timeout -= 1
    
    # Tjek om forbindelsen lykkedes
    if not wlan.isconnected():
        raise Exception("WiFi timeout efter {}sekunder".format(WIFI_TIMEOUT))
    
    print("WiFi forbundet")
    print("IP adresse: {}".format(wlan.ifconfig()[0]))
    
    return wlan


# MQTT funktioner

def send_status(klient):
    """
    Sender vindues status via MQTT.
    
    Args:
        klient: MQTTClient objekt
    
    Raises:
        Exception: Hvis MQTT publish fejler
    
    Payload Format:
        {
            "status": "aaben" | "lukket",
            "position": <int>,
            "max_position": <int>
        }
    
    Note:
        Kaldes efter hver vinduesoperation for at opdatere frontend.
    """
    try:
        status_data = json.dumps({
            'status': vindue_status,
            'position': nuværende_position,
            'max_position': STEPS_HELT_ÅBEN
        })
        
        klient.publish(MQTT_TOPIC_STATUS, status_data)
        print("Status sendt: {}".format(status_data))
        
    except Exception as fejl:
        raise Exception("Status send fejl: {}".format(str(fejl)))


def mqtt_callback(topic, msg, stepper_pins, solenoid, buzzer, klient):
    """
    Callback funktion til MQTT besked håndtering.
    
    Args:
        topic: MQTT topic (vi bruger den ikke men den skal være der)
        msg: MQTT payload (bytes)
        stepper_pins: Liste af Pin objekter
        solenoid: Pin objekt
        buzzer: PWM objekt
        klient: MQTTClient objekt
    
    Kommando Routing:
        JSON payload med 'kommando' key routes til relevant funktion:
        - 'aaben' -> åben_vindue()
        - 'luk' -> luk_vindue()
        - 'kort_aaben' -> kort_åben_vindue()
        - 'manuel_aaben' -> manuel_åben()
        - 'manuel_luk' -> manuel_luk()
    
    Error Handling:
        Alle exceptions ignoreres (pass) for at undgå at callback
        crasher MQTT loop. Fejl logges til konsol.
    
    Note:
        Status sendes efter hver kommando (undtagen kort_aaben som
        sender sin egen status).
    """
    try:
        # Decode og parse JSON
        data = json.loads(msg)
        kommando = data.get('kommando', '')
        
        print("MQTT kommando modtaget: {}".format(kommando))
        
        # Rute for "kommando"
        if kommando == 'aaben':
            åben_vindue(stepper_pins, solenoid, buzzer)
            send_status(klient)
        
        elif kommando == 'kort_aaben':
            kort_åben_vindue(stepper_pins, solenoid, buzzer, klient)
            # Status sendes internt af kort_åben_vindue
        
        elif kommando == 'luk':
            luk_vindue(stepper_pins, solenoid, buzzer)
            send_status(klient)
        
        elif kommando == 'manuel_aaben':
            manuel_åben(stepper_pins, solenoid)
            send_status(klient)
        
        elif kommando == 'manuel_luk':
            manuel_luk(stepper_pins, solenoid)
            send_status(klient)
        
        else:
            print("Ukendt kommando: {}".format(kommando))
    
    except Exception as fejl:
        print("Callback fejl: {}".format(fejl))
        # Gå videre efter - fortsæt MQTT loop


# Main funktionen

def main():
    """
    Vores hovedfunktion - starter MQTT listener loop.
    
    Sekvebs:
        1. Initialiser hardware
        2. Forbind til WiFi
        3. Opret MQTT client
        4. Subscribe til kommando topic
        5. Send vinduesstatus
        6. Poll loop (check_msg hver 100ms)
        7. Ved fejl: Reconnect logic med genforsøg
    
    Reconnect Logic:
        Ved MQTT eller WiFi fejl forsøges der at skabes forbindelse igen efter 5 sekunders delay.
        Uendelig forsøg sikrer at systemet kommer tilbage efter fejl.
    
    Poll Loop:
        client.check_msg() kaldes kontinuerligt med 100ms sleep mellem.
        Dette tillader hurtig respons på MQTT kommandoer.
    
    Note:
        Denne funktion kører i infinite loop. ESP32 reboot krævet for stop.
    """
    try:
        # 1. Initialiser hardware
        stepper_pins, solenoid, buzzer = opsæt_hardware()
        
        # 2. Forbind til WiFi
        forbind_wifi()
        
        # 3. Opret MQTT client
        klient = MQTTClient(ENHEDS_ID, MQTT_SERVER)
        
        # 4. Opsæt callback wrapper med hardware references
        def callback_wrapper(topic, msg):
            '''Vi skal bruge callback wrapper for at komponenterne ved at de skal arbejde'''
            mqtt_callback(topic, msg, stepper_pins, solenoid, buzzer, klient)
        
        klient.set_callback(callback_wrapper)
        klient.connect()
        
        print("MQTT forbundet til broker: {}".format(MQTT_SERVER))
        
        # 5. Subscribe til command topic
        klient.subscribe(MQTT_TOPIC_COMMAND)
        print("Subscribed til: {}".format(MQTT_TOPIC_COMMAND))
        
        # 6. Send initial status
        send_status(klient)
        
        # 7. Poll loop
        while True:
            try:
                # Tjek for MQTT beskeder
                klient.check_msg()
                
                # Sleep mellem polls
                sleep(MQTT_CHECK_INTERVAL)
                
            except Exception as fejl:
                # Poll fejl - forsøg at genskabe forbindelse til Broker
                print("Poll fejl: {}".format(fejl))
                
                # Disconnect
                try:
                    klient.disconnect()
                except:
                    pass
                
                # Delay
                sleep(5)
                
                # Reconnect
                try:
                    print("Reconnecting til MQTT")
                    forbind_wifi()
                    klient = MQTTClient(ENHEDS_ID, MQTT_SERVER)
                    klient.set_callback(callback_wrapper)
                    klient.connect()
                    klient.subscribe(MQTT_TOPIC_COMMAND)
                    print("Reconnect succesfuld")
                    
                except:
                    # Giver en længere cooldown før retry af while True loopet igen
                    sleep(10)
    
    except Exception as fejl:
        # Ukendt fejl
        print("Større fejl: {}".format(fejl))
        
        # Slukker alt
        try:
            sluk_stepper_motor(stepper_pins)
            deaktiver_solenoid(solenoid)
            stop_buzzer(buzzer)
        except:
            pass
        
        # Prøver igen efter 10 sekunder
        sleep(10)


# Start af kode

if __name__ == "__main__":
    """
    Entry point til koden
    
    MicroPython execution model:
        1. Boot -> kør boot.py (system initering)
        2. Kør main.py (dette script)
        3. main() kaldes i infinite loop
        4. Ved crash -> main() kaldes igen efter delay
    
    Uendelig forsøg:
        Vores while True loop sikrer at main() altid kaldes igen efter
        crash.
    
    Kalibrerings procedure:
        1. Upload script til ESP32
        2. Sørg for vindue er manuelt lukket
        3. Åbn serial monitor (shell)
        4. Send 'aaben' kommando via MQTT
        5. Mål faktisk åbnings-distance
        6. Tilpas STEPS_HELT_ÅBEN
        7. Genupload og test igen
    
    VIGTIGT:
        Ved første opstart skal vindue være manuelt lukket da
        nuværende_position initialiseres til 0 (lukket).
    """
    # Uendeligt loop for auto-recovery
    while True:
        try:
            main()
        except KeyboardInterrupt:
            # Manual stop via Ctrl+C (kun ved USB serial)
            break
        except:
            # Fejl eller crash - retry efter 10 sekunder.
            sleep(10)