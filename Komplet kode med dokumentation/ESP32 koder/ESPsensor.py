"""
Vores ESP32 kode til vores udendørs løsning

Denne micropyhton kode implementerer:
- Temperatur- og fugtighedsmålinger fra DHT11 sensor
- Overvågning af batteriprocent
- MQTT kommunikation til Lokal server (RPi5)
- Power saving med deep sleep
- Automatisk retry ved fejl

Hardware:
- ESP32 DevKitc v4
- DHT11 sensor (Pin 16)
- LiPo 3.7V batteri

Power Management:
- Normal operation: Deep sleep mellem målinger (1200s)
- Lavt battery ( under 5%): Deep sleep i 18 timer
- Vi benytter Deep sleep så den automatisk vågner op igen

MQTT Topics:
- sensor/temperatur: Temperatur målinger (°C)
- sensor/luftfugtighed: Luftfugtigheds målinger (%)
- sensor/batteri: Batteriniveau (%)
- fejl: Fejlbeskeder

Måle-interval:
- 900 sekunder (15 minutter) mellem målinger for at spare på batteriet.
    
Batteri Beskyttelse:
- Ved <5% batteri går ESP32 i en lang deepsleep sleep
  i håb om at kunne oplade lidt fra solcellen

Note:
- Da det er micropython vi arbejder i, giver det ikke mening at benytte type-hints
  Da det ikke lagres i ESP'ens interne hukommelse.
"""

from machine import Pin, ADC, deepsleep
from time import sleep
import dht
from umqtt.simple import MQTTClient
import network
import json

# Konfiguration af MQTT

MQTT_SERVER = '192.168.4.1'
"""MQTT broker IP adresse (RPi5 AP)."""

ENHEDS_ID = 'esp32_sensor'
"""Unik identifier til MQTT broker."""

MQTT_TOPIC_TEMP = 'sensor/temperatur'
"""MQTT topic til temperatur data."""

MQTT_TOPIC_FUGT = 'sensor/luftfugtighed'
"""MQTT topic til luftfugtigheds data."""

MQTT_TOPIC_BAT = 'sensor/batteri'
"""MQTT topic til batteriniveau i procent."""

MQTT_TOPIC_FEJLBESKED = 'fejlbesked'
"""MQTT topic til fejlbeskeder."""

MQTT_TIMEOUT = 5
"""Timeout i sekunder for MQTT operations."""

# Konfiguration af Wi-Fi

SSID = 'RaspberryPi_AP'
"""WiFi SSID (Navn)."""

PASSWORD = 'RPI12345'
"""WiFi adgangskode."""

WIFI_TIMEOUT = 20
"""Timeout i sekunder vi max venter på WiFi forbindelse."""

# Konfiguration af hardware pins

DHT11_PIN = 16
"""GPIO pin til DHT11 sensor."""

BATTERI_ADC_PIN = 34
"""GPIO pin til ADC-værdier fra batteriet."""

# Konfiguration af vores batteri

BATTERI_MAX_VOLT = 3.842
"""Maximum batteri voltage (Fuld opladt)."""

BATTERI_MAX_ADC = 2380
"""ADC værdi ved max voltage."""

BATTERI_MIN_VOLT = 3.0
"""Minimumsgrænse for batteri voltage (undgå komplet afladning)."""

BATTERI_MIN_ADC = int((BATTERI_MIN_VOLT / BATTERI_MAX_VOLT) * BATTERI_MAX_ADC)
"""ADC værdi ved min voltage (beregnet)."""

BATTERI_SHUTDOWN_GRÆNSE = 5
"""Batteri procent hvor ESP32 går i 18 timers Deepsleep."""

# Konfiguration af vores tidsintervaller

SLEEP_TID = 900*1000
"""Sekunder mellem sensor målinger (15 minutter)."""

GENFORSØGS_DELAY = 5
"""Tid i sekunder vi venter før retry ved fejl."""

# Konfiguration af vores DHT11 sensor

DHT11_MAX_FORSØG = 3
"""Max antal forsøg at læse DHT11 sensor."""

DHT11_FORSØGS_DELAY = 2
"""Sekunder mellem DHT11 læsningsforsøg."""


# Funktioner til vores Wi-Fi

def forbind_til_wifi():
    """
    Forbinder til WiFi Access Point med timeout.
    
    Aktiverer WiFi station mode og forbinder til konfigureret SSID.
    Station mode betyder at den skal forbinde og ikke selv være et AP
    Venter op til 20 sekunder fra WIFI_TIMEOUT med at få forbindelse
    
    Returns:
        ESP32 bliver et WLAN-objekt hvis forbindelsen er succesfuld
    
    Raises:
        Exception: Hvis WiFi forbindelse fejler efter timeout-tiden
    
    Note:
        WiFi forbliver aktiv efter denne funktion.
        Hvis vi ønskede mere power saving ville vi tilføje wlan.active(false)
        Inden vi gik i Deepsleep, og så forbinde på ny ved wake-up
    """
    # Print for debug
    print("Forbinder til WiFi: {}".format(SSID))
    
    # Initialiser WiFi station mode
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    
    # Tjek om den er forbundet
    if wlan.isconnected():
        # Prints for debug
        print("WiFi allerede forbundet")
        print("IP adresse: {}".format(wlan.ifconfig()[0]))
        return wlan
    
    # Start forbindelsesprocess
    wlan.connect(SSID, PASSWORD)
    
    # Vent på det oprettes forbindelse og opsæt vores timeout timer
    timeout = WIFI_TIMEOUT
    while not wlan.isconnected() and timeout > 0:
        sleep(1)
        timeout -= 1
    
    # Tjek om der blev skabt forbindelse
    if not wlan.isconnected():
        # Stopper koden og sender en fejl besked i konsollen
        raise Exception("WiFi forbindelse timeout efter {}s".format(WIFI_TIMEOUT))
    
    # Til debug
    print("WiFi forbundet")
    print("IP adresse: {}".format(wlan.ifconfig()[0]))
    
    # Returnerer wlan så koden nu ved at der er forbindelse
    return wlan


# Her kommer vores funktioner til ADC-aflæsning og DHT11-målinger

def læs_batteri():
    """
    Læser batteri niveau via ADC og konverterer til procent.
    
    Hardware setup:
        Der sidder en spændingsdeler inden ADC-gpio pin
        Dette er fordi at ESP32'eren ikke tåler over 3.3V
        På sine Gpio pins
    
    Returns:
        Batteriets niveau i procent (0-100)
    
    Kalibrering:
        BATTERI_MAX_ADC er målt ved fuldt opladet batteri (3.842 V).
    
    Note:
        ADC på ESP32 er ikke særlig præcis.
        Der bruges 11dB attenuation så ADC'ens "rækkevidde" gøres bredere.
    """
    # Initialiser ADC med 11dB attenuation
    batteri_adc = ADC(Pin(BATTERI_ADC_PIN))
    batteri_adc.atten(ADC.ATTN_11DB)
    
    # Læs rå ADC værdi
    adc_værdi = batteri_adc.read()
    
    # Debug
    print("Batteri ADC: {}".format(adc_værdi))
    
    # Håndterer de yderste værdier først
    if adc_værdi == 0:
        # ADC læsning fejlede eller intet batteri
        return 0
    elif adc_værdi <= BATTERI_MIN_ADC:
        # Under vores sikkerhedsgrænse
        return 0
    elif adc_værdi >= BATTERI_MAX_ADC:
        # Fuldt opladet og/eller over 2240
        return 100
    
    # Her laves der en matematisk lineær interpolation
    batteri_procent = int(
        ((adc_værdi - BATTERI_MIN_ADC) / (BATTERI_MAX_ADC - BATTERI_MIN_ADC)) * 100
    )
    
    # Vi ønsker ikke værdier der går over 100 eller under 0
    batteri_procent = max(0, min(100, batteri_procent))
    
    # Debug
    print("Batteri niveau: {}%".format(batteri_procent))
    
    return batteri_procent


def læs_dht11_data():
    """
    Læser temperatur og fugtighed fra DHT11 sensor med genforsøg ved problemer.
    
    DHT11 Specifikationer:
        - Temperatur: 0-50°C (±2°C accuracy)
        - Fugtighed: 20-90% RH (±5% accuracy)
        - Sampling rate: Maximum 1Hz (1 måling per sekund)
    
    Returns:
        Tuple med (temperatur, fugtighed) i (°C, %)
    
    Raises:
        Exception: Hvis sensor læsning fejler efter max forsøg
    
    Retry Logic:
        DHT11 kan fejle pga. timing issues. Ved OSError genforsøges der op til
        DHT11_MAX_FORSØG (3) gange med DHT11_GENFORSØGS_DELAY (2) sekunder mellem forsøg.
    
    Note:
        DHT11 er upræcis - vi ville benytte minimum DHT22 i professionel løsning
    """
    # Debug
    print("Læser DHT11 sensor")
    
    # Aktiverer DHT11 og kobler den på sensor objekt
    sensor = dht.DHT11(Pin(DHT11_PIN))
    
    # Genforsøgsloop
    for forsøg in range(DHT11_MAX_FORSØG):
        try:
            # Laver måling
            sensor.measure()
            
            # Læs værdier fra målingen
            temp = sensor.temperature()
            hum = sensor.humidity()
            
            # Debug
            print("Temperatur: {}°C".format(temp))
            print("Fugtighed: {}%".format(hum))
            
            return temp, hum
        
            # I/O error
        except OSError as fejl:
            print("DHT11 fejl (forsøg {}/{}): {}".format(
                forsøg + 1, DHT11_MAX_FORSØG, fejl
            ))
            
            # Vent før det forsøges igen (undtagen sidste forsøg)
            if forsøg < DHT11_MAX_FORSØG - 1:
                sleep(DHT11_FORSØGS_DELAY)
    
    # Alle forsøg fejlede
    raise Exception("DHT11 læsning fejlede efter {} forsøg".format(
        DHT11_MAX_FORSØG
    ))


# MQTT funktioner

def publicer_mqtt(topic, data):
    """
    Publisher JSON data til MQTT topic med error-handling.
    
    Opretter ny MQTT connection for hver publish (stateless).
    Dette kræver mere strøm, men da deepsleep slukker for
    WiFi antennen er det nødvendigt og der spares mere
    strøm på den lange bane.
    
    Args:
        topic: MQTT topic string
        data: Dictionary at konvertere til JSON
    
    Returns:
        True hvis publish succesfuld, False hvis fejl
    
    Error Handling:
        Alle exceptions catches og returnerer False. Dette tillader
        caller at fortsætte selvom én publish fejler.
    """
    klient = None
    
    try:
        print("Publisher til {}: {}".format(topic, data))
        
        # Opret os selv som en MQTT klient
        klient = MQTTClient(ENHEDS_ID, MQTT_SERVER)
        
        # Forbind til broker
        klient.connect()
        
        # konverter data til JSON format
        payload = json.dumps(data)
        
        # Publish besked QoS=1 da vi gerne vil have "kvittering" for modtagelse
        klient.publish(topic, payload, qos=1)
        
        # Graceful disconnect
        klient.disconnect()
        
        # debug
        print("MQTT publish succesfuld")
        return True
        
    except Exception as fejl:
        print("MQTT publish fejl: {}".format(fejl))
        
        # Vores error-handling til hvis der ikke bliver disconnectet korrekt
        if klient:
            try:
                klient.disconnect()
            except:
                pass
        
        return False


def send_fejl(fejl_besked):
    """
    Sender fejlbesked til MQTT "fejl" topic.
    
    Hvis det fejler at vi sender besked om fejl, så ignorerer vi det
    
    Args:
        fejl_besked: Fejlbeskrivelse string
    
    Note:
        Denne funktion må ikke raises da det er den som "sluger"
        alle vores fejl
    """
    try:
        fejl_data = {
            'fejl': fejl_besked,
            'enhed': ENHEDS_ID
        }
        publicer_mqtt(MQTT_TOPIC_FEJLBESKED, fejl_data)
    except:
        # Her ignorerer vi fejl
        pass


# Funktion til at redde batteriet

def luk_ned():
    """
    Går i 18-timers deep sleep mode.
    
    Deepsleep mode:
        - Strømforbrug: 10 - 150µA
        - WiFi og CPU slukket
        - Kun RTC og ULP co-processor aktiv
        - Wake-up kun via hardware reset eller timer
    
    Batteribeskyttelse:
        Deepsleep bruger vi til forhåbentlig at forhindrer 
        deep discharge af LiPo batteriet som kan
        permanent ødelægge batteriet. ESP32 vågner selv efter
        18 timer, hvor at solcellen forhåbentlig har opladt det
    
    Note:
        64800 sekunder svarer til 18 timer
    """
    # Debug
    print("Problem: Batteri under {}% - går i deepsleep".format(
        BATTERI_SHUTDOWN_GRÆNSE
    ))
    
    # Vil forsøge at sende besked til RPi5 om problemet
    try:
        send_fejl("Lavt batteri: (<{}%)".format(
            BATTERI_SHUTDOWN_GRÆNSE
        ))
        sleep(1)
    except:
        pass
    
    # 18 timers deepsleep
    deepsleep(64800)


# "Rå" funktion til målinger

def _udfør_måling():
    """
    Udfører komplet måle-cyklus og sender data via MQTT.
    
    Måle-flow:
        1. Læs batteri niveau
        2. Tjek for lavt batteri - deepsleep hvis under grænsen
        3. Forbind til WiFi
        4. Læs DHT11 sensor
        5. Send alle tre målinger via MQTT
    
    Raises:
        Exception: Ved  fejl som WiFi eller sensor der forhindrer måling
    
    Note:
        Denne funktion bruges i både normal flow og i et genforsøgsflow.
    """
    # debug
    print("Starter måle-cyklus")
    
    # 1. Læs batteri niveau først
    batteri_procent = læs_batteri()
    
    # 2. Tjek for lavt batteri
    if batteri_procent <= BATTERI_SHUTDOWN_GRÆNSE:
        luk_ned()
    
    # 3. Forbind til WiFi
    forbind_til_wifi()
    
    # 4. Læs sensor data
    temp, fugt = læs_dht11_data()
    
    # 5. Send alle målinger via MQTT
    print("Sender data til MQTT broker")
    
    publicer_mqtt(MQTT_TOPIC_TEMP, {'temperatur': temp})
    publicer_mqtt(MQTT_TOPIC_FUGT, {'luftfugtighed': fugt})
    publicer_mqtt(MQTT_TOPIC_BAT, {'batteri': batteri_procent})
    
    print("Måle-cyklus fuldført")

# Wrapper om den "Rå" funktion
def udfør_måling_med_retry():
    """
    Wrapper omkring udfør_måling() med error handling og retry.
    
    Error Handling Flow:
        1. Forsøg normal måling
        2. Ved fejl: Log fejlen og send til MQTT
        3. Vent GENFORSØGS_DELAY (5) sekunder
        4. Forsøg måling igen (andet forsøg - første genforsøg)
        5. Ved anden fejl: Log og fortsæt til deepsleep
    
    Rationale:
        Transiente fejl (WiFi timeout, DHT11 timing) kan ofte løses
        med en enkelt retry. Efter to fejl antages det at der er et
        mere fundamentalt problem (sensor hardware, netværk down).
    
    Note:
        Efter retry går ESP32 i sleep uanset outcome. Dette sikrer at
        systemet ikke hænger i infinite retry loop og dræner batteri.
    """
    try:
        # Første forsøg
        _udfør_måling()
        
    except Exception as fejl:
        # Måling fejlede
        fejl_besked = str(fejl)
        print("FEJL under måling: {}".format(fejl_besked))
        
        # Send fejl til MQTT (hvis ikke MQTT relateret)
        if 'MQTT' not in fejl_besked:
            send_fejl(fejl_besked)
        
        # Vent før der forsøges igen
        print("Venter {}s før der forsøges igen".format(GENFORSØGS_DELAY))
        sleep(GENFORSØGS_DELAY)
        
        # Genforsøgs måling
        print("Forsøger igen")
        try:
            _udfør_måling()
            print("succesfuld")
            
        except Exception as fejl2:
            # Anden fejl - giv op
            fejl_besked2 = str(fejl2)
            print("Fejl ved andet forsøg: {}".format(fejl_besked2))
            
            # Send anden fejl til MQTT
            if 'MQTT' not in fejl_besked2:
                send_fejl("fejl ved andet forsøg: {}".format(fejl_besked2))
            # debug
            print("Fortsætter til sleep efter to fejl")


# Vores Main funktion

def main():
    """
    Main funktionen - her sættes det hele sammen
    
    Program Flow:
        1. Initial delay (1 sekund for stabilisering)
        2. Udfør måling med genforsøg
        3. Deepsleep i SLEEP_TID (900) sekunder
        4. Wake-up og repeat
    
    Infinite Loop:
        main() kaldes kontinuerligt af MicroPython runtime efter hvert
        wake-up.
    """
    # debug
    print("ESP32 tænder")
    print("Sleep interval: {}s ({} min)".format(SLEEP_TID, SLEEP_TID // 60))
    print("Batteri slukker: <{}%".format(BATTERI_SHUTDOWN_GRÆNSE))
    
    # Stabiliserings delay
    sleep(1)
    
    # Udfør måling med error handling
    udfør_måling_med_retry()
        
    # Deepsleep til næste måling
    print("\nGår i deepsleep i {}s ({} min)...".format(
        SLEEP_TID, SLEEP_TID // 60
    ))
        
    deepsleep(SLEEP_TID)
        
    # Wake-up sker automatisk efter deepsleep
    # MicroPython runtime kalder main() igen

"""
Planlagt execution model:
    1. Power on / Wake-up -> boot.py køres (system init)
    2. main.py køres (dette script)
    3. main() kaldes
    4. main() kalder deepsleep() -> ESP32 slukker
    5. RTC timer vækker ESP32 efter SLEEP_TID
    6. Tilbage til step 1 efter reboot
"""
if __name__ == "__main__":
    while True:
        try:
            main()
        except Exception:
            deepsleep(60*1000)