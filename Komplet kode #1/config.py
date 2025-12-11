import os

# Opsætning og topics til vores MQTT - henter fra os (.env fil) så vi ikke har alt hardcodet
# Kan være vi opdaterer til noget bedre end .env hvis der er tid til det
MQTT_BROKER_HOST = os.getenv('MQTT_BROKER_HOST', 'localhost')
MQTT_BROKER_PORT = int(os.getenv('MQTT_BROKER_PORT', '1883'))

TOPIC_SENSOR_TEMP = "sensor/temperature"
TOPIC_SENSOR_HUM = "sensor/humidity"
TOPIC_SENSOR_BAT = "sensor/battery"
TOPIC_VINDUE_COMMAND = "vindue/command"
TOPIC_VINDUE_STATUS = "vindue/status"
TOPIC_ERROR = 'error'

# Intervallet som BME680 sensoren måler med
BME680_MAALINGS_INTERVAL = 10 

# Konfiguration af vores Webserver
WEB_HOST = "0.0.0.0"
WEB_PORT = 8000

# Vores Thresholds dictionary, dette er grænserne vi bruger til at bedømme indeklimaet
THRESHOLDS = {
    'temp': {
        'limit_low': 19,
        'limit_high': 22,
        'max': 25
    },
    'humidity': {
        'limit_low': 40,
        'limit_high': 60,
        'max': 75
    },
    'gas': {
        'limit_line': 45000,
        'min': 25000,
    }
}

# Dette skal rettes for at tilpasses vores API-kalds-indstillinger - skal laves i en .env fil
REMOTE_SERVER_URL = os.getenv('REMOTE_SERVER_URL', '')
BEARER_TOKEN = os.getenv('BEARER_TOKEN', '')
# Intervel mellem vores synkronisering med vores remote server
SYNC_INTERVAL = int(os.getenv('SYNC_INTERVAL', '300'))
#ID til vores RPI så serveren ved hvor data stammer fra:
DEVICE_ID = os.getenv('DEVICE_ID', 'rpi5_id_1')