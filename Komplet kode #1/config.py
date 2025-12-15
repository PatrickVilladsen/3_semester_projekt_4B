"""
Konfigurationsfil til automatisk vindues-styring system.

Dette modul indeholder alle konfigurerbare parametre for systemet:
- MQTT broker forbindelse og topics
- Sensor måle-intervaller
- Webserver indstillinger
- Klima grænseværdier for automatik
- Remote server synkroniserings konfiguration

Environment Variabler:
    Følgende værdier kan overskrives via .env fil:
    - MQTT_BROKER_HOST: IP/hostname til MQTT broker
    - REMOTE_SERVER_URL: URL til remote FastAPI server
    - BEARER_TOKEN: Authentication token til remote server
    - SYNKRONISERINGS_INTERVAL: Sekunder mellem synkroniseringer
    - ENHEDS_ID: Unik identifikator til denne RPi5

Konfigurationsgrundlag:
    - Hardcodede defaults for lokal udvikling
    - Environment variabler til produktion og øget sikkerhed
    - Ingen secrets i kode på nuværende tidspunkt
    - Klima grænseværdier kan justeres centralt

Note:
    Da det er Python vi arbejder med, bruger vi type hints for klarhed.
"""

import os
from typing import Dict, Any


# Konfiguration af MQTT broker

MQTT_BROKER_HOST: str = os.getenv('MQTT_BROKER_HOST', 'localhost')
"""
MQTT broker hostname eller IP adresse.

Default: 'localhost' for lokal udvikling
IP: '192.168.4.1' (RPi5 Access Point)
"""

MQTT_BROKER_PORT: int = int(os.getenv('MQTT_BROKER_PORT', '1883'))
"""
MQTT broker port nummer.

Default: 1883 (standard MQTT port)
Secure MQTT (TLS): Typisk 8883
"""


# Konfiguration af MQTT topics

TOPIC_SENSOR_TEMP: str = "sensor/temperatur"
"""Udendørs ESP32 sensor temperatur topic."""

TOPIC_SENSOR_FUGT: str = "sensor/luftfugtighed"
"""Udendørs ESP32 sensor fugtigheds topic."""

TOPIC_SENSOR_BAT: str = "sensor/batteri"
"""Udendørs ESP32 sensor batteri topic."""

TOPIC_VINDUE_KOMMANDO: str = "vindue/kommando"
"""Kommando topic til ESP32 vindues-kontrol."""

TOPIC_VINDUE_STATUS: str = "vindue/status"
"""Status topic fra ESP32 vindues-kontrol."""

TOPIC_FEJLBESKED: str = "fejlbesked"
"""Fejlbeskeder fra alle ESP32 enheder."""


# Konfiguration af sensor

BME680_MÅLINGS_INTERVAL: int = 10
"""Sekunder mellem BME680 sensor målinger."""


# Konfiguration af webserver

WEB_HOST: str = "127.0.0.1"
"""
Webserver bind-adress.

'0.0.0.0': Tilgængelig på alle netværks interfaces
'127.0.0.1': Kun tilgængelig lokalt

Default: '127.0.0.1' for at tillade access fra RPi5 selv men ikke eksterne devices
"""

WEB_PORT: int = 8000
"""
Webserver TCP port.

Default: 8000 (standard development port)
Produktion: Typisk 80 (HTTP) eller 443 (HTTPS) med reverse proxy

Note:
    Ports under 1024 kræver root privileges på Linux.
"""


# Konfiguration af klima grænseværdier

GRÆNSER: Dict[str, Dict[str, Any]] = {
    'temp': {
        'limit_low': 19,    # °C - Under dette er for koldt
        'limit_high': 22,   # °C - Over dette er for varmt
        'max': 25           # °C - Grænse for kort_aabning
    },
    'luftfugtighed': {
        'limit_low': 40,    # % - Under dette er for tørt
        'limit_high': 60,   # % - Over dette er for fugtigt
        'max': 75           # % - Grænse for kort_aabning
    },
    'gas': {
        'grænse': 45000,    # Ohm - Under dette = begyndende dårlig luftkvalitet
        'min': 25000        # Ohm - Under dette = meget dårlig luftkvalitet
    }
}
"""
Klima grænseværdier for automatisk vindues styring.

Temperatur Zoner:
    < 19°C:     For koldt - luk vindue hvis åbent
    19-22°C:    Optimal komfort zone
    22-25°C:    For varmt - åbn vindue hvis lukket
    > 25°C:     Alt for varmt - aktiver kort_aabning

Fugtigheds Zoner:
    < 40%:      For tørt - åbn vindue hvis lukket
    40-60%:     Optimal komfort zone
    60-75%:     For fugtigt - åbn vindue hvis lukket
    > 75%:      Alt for fugtigt - aktiver kort_aabning

Gas Modstand (Luftkvalitet):
    > 50k Ohm:  God luftkvalitet
    45-50k:     Acceptabel luftkvalitet
    25-45k:     Dårlig luftkvalitet (VOC) - åbn vindue
    < 25k:      Meget dårlig luftkvalitet - aktiver kort_aabning 

Justerings Guide:
    - Temperatur: ±1-2°C baseret på komfort
    - Fugtighed: Juster efter årstid, indeklima er fugtigst sommer og tørrest vinter
    - Gas: Kræver sensor kalibrering

Note:
    Disse værdier er optimeret for danske guides om det optimale indeklima
"""


# Konfiguration af remote server oplysninger

REMOTE_SERVER_URL: str = os.getenv('REMOTE_SERVER_URL', '')
"""
URL til remote server for data opbevaring.

Format: 'URL'
Tomt: Data opbevares kun lokalt

Note:
    Hvis der ikke er opsat URL vil sync_client springe upload over 
    men data vil fortsætte med at blive gemt lokalt i database.
"""

BEARER_TOKEN: str = os.getenv('BEARER_TOKEN', '')
"""
Authentication token til remote server API.

Format: JWT eller API key string
Sendes som: 'Authorization: Bearer <token>' header

Security Note:
    Token må ikke hardcodes i koden.
"""

SYNKRONISERINGS_INTERVAL: int = int(os.getenv('SYNKRONISERINGS_INTERVAL', '300'))
"""
Sekunder mellem remote server synkroniserings forsøg.

Note:
    Ved netværksfejl bruges exponential backoff:
    Forsøg 1: 5 min, Forsøg 2: 10 min, Forsøg 3: 20 min,
    Forsøg 4: 40 min, Forsøg 5+: 60 min (max)
"""

ENHEDS_ID: str = os.getenv('ENHEDS_ID', 'rpi5_id_1')
"""
Unik identifikator for denne RPi5.

Format: Fri tekst string, typisk 'rpi5_id_<nummer>'
Bruges til: At identificere data kilde på remote server

Eksempler:
    'rpi5_id_BallerupSkole32': Ballerup skole lokale 32
    'rpi5_id_BallerupSkole33': Ballerup skole lokale 33
    'rpi5_id_NordstjerneskolenH3': Nordstjerneskolen lokale H3

Note:
    Hvis flere RPi'er sender til samme remote server, skal hver
    have unikt ENHEDS_ID for at kunne differentiere datakilder.
"""