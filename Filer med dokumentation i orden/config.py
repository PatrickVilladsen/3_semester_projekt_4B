# Opsætning og topics til vores MQTT
MQTT_BROKER_HOST = "localhost"
MQTT_BROKER_PORT = 1883

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

# Dette skal rettes for at tilpasses vores API-kalds-indstillinger
REMOTE_SERVER_URL = ""
BEARER_TOKEN = ""
# Intervel mellem vores synkronisering med vores remote server
SYNC_INTERVAL = 300