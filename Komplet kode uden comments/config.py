

import os
from typing import Dict, Any



MQTT_BROKER_HOST: str = os.getenv('MQTT_BROKER_HOST', 'localhost')


MQTT_BROKER_PORT: int = int(os.getenv('MQTT_BROKER_PORT', '1883'))




TOPIC_SENSOR_TEMP: str = "sensor/temperatur"


TOPIC_SENSOR_FUGT: str = "sensor/luftfugtighed"


TOPIC_SENSOR_BAT: str = "sensor/batteri"


TOPIC_VINDUE_KOMMANDO: str = "vindue/kommando"


TOPIC_VINDUE_STATUS: str = "vindue/status"


TOPIC_FEJLBESKED: str = "fejlbesked"




BME680_MÅLINGS_INTERVAL: int = 10




WEB_HOST: str = "127.0.0.1"


WEB_PORT: int = 8000




GRÆNSER: Dict[str, Dict[str, Any]] = {
    'temp': {
        'limit_low': 19,
        'limit_high': 22,
        'max': 25
    },
    'luftfugtighed': {
        'limit_low': 40,
        'limit_high': 60,
        'max': 75
    },
    'gas': {
        'limit_line': 45000,
        'min': 25000
    }
}




REMOTE_SERVER_URL: str = os.getenv('REMOTE_SERVER_URL', '')


BEARER_TOKEN: str = os.getenv('BEARER_TOKEN', '')


SYNKRONISERINGS_INTERVAL: int = int(os.getenv('SYNKRONISERINGS_INTERVAL', '300'))


ENHEDS_ID: str = os.getenv('ENHEDS_ID', 'rpi5_id_1')
