
import signal
import sys
import traceback
from typing import Optional, NoReturn

from indoor_sensor import bme680_sensor
from mqtt import mqtt_klient
from sync_client import sync_klient
from database import db
from config import ENHEDS_ID


sensor_reference: Optional[object] = None



def signal_handler(sig: int, frame: Optional[object]) -> NoReturn:

    signal_navn = 'SIGINT' if sig == signal.SIGINT else 'SIGTERM'
    db.gem_system_log(ENHEDS_ID, 'Main', f'Shutdown initieret via {signal_navn}')
    
    print(f"\n{signal_navn} starter graceful shutdown")
    
    if sensor_reference is not None:
        try:
            print("Stopper BME680 sensor")
            sensor_reference.stop()
            db.gem_system_log(ENHEDS_ID, 'Main', 'BME680 sensor stoppet')
        except Exception as fejl:
            db.gem_fejl(ENHEDS_ID, 'Main', f"Fejl ved stop af BME680: {fejl}")
    
    try:
        print("Stopper MQTT client")
        mqtt_klient.stop()
        db.gem_system_log(ENHEDS_ID, 'Main', 'MQTT client stoppet')
    except Exception as fejl:
        db.gem_fejl(ENHEDS_ID, 'Main', f"Fejl ved stop af MQTT: {fejl}")
    
    try:
        print("Stopper sync client")
        sync_klient.stop()
        db.gem_system_log(ENHEDS_ID, 'Main', 'Sync client stoppet')
    except Exception as fejl:
        db.gem_fejl(ENHEDS_ID, 'Main', f"Fejl ved stop af sync: {fejl}")
    
    try:
        print("Rydder gammel database data")
        db.ryd_gammel_data(dage=7)
        db.gem_system_log(ENHEDS_ID, 'Main', 'Database cleanup udført (7 dage)')
    except Exception as fejl:
        db.gem_fejl(ENHEDS_ID, 'Main', f"Database cleanup fejl: {fejl}")
    
    print("Graceful shutdown fuldført")
    db.gem_system_log(ENHEDS_ID, 'Main', 'Graceful shutdown fuldført')
    sys.exit(0)


def registrer_signal_handlers() -> None:

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    db.gem_system_log(ENHEDS_ID, 'Main', 'Signal handlers registreret')


def initialiser_bme680_sensor() -> object:

    try:
        bme680_sensor.start()
        
        from app import notificer_websocket_klienter
        bme680_sensor.sæt_websocket_callback(notificer_websocket_klienter)
        
        bme680_sensor.sæt_mqtt_klient(mqtt_klient)
        
        db.gem_system_log(ENHEDS_ID, 'Main', 'BME680 sensor startet')
        return bme680_sensor
        
    except Exception as fejl:
        fejl_besked = f"BME680 initialiserings fejl: {fejl}"
        db.gem_fejl(ENHEDS_ID, 'Main', fejl_besked)
        raise RuntimeError(fejl_besked)


def initialiser_mqtt_klient() -> None:

    try:
        from app import notificer_websocket_klienter
        mqtt_klient.sæt_websocket_callback(notificer_websocket_klienter)
        
        mqtt_klient.start()
        
        db.gem_system_log(ENHEDS_ID, 'Main', 'MQTT client startet')
        
    except Exception as fejl:
        fejl_besked = f"MQTT initialiserings fejl: {fejl}"
        db.gem_fejl(ENHEDS_ID, 'Main', fejl_besked)
        raise RuntimeError(fejl_besked)


def initialiser_sync_klient() -> None:

    try:
        sync_klient.start()
        
        db.gem_system_log(
            ENHEDS_ID,
            'Main',
            'Sync client startet (30 sekunders initial delay)'
        )
        
    except Exception as fejl:
        fejl_besked = f"Sync initialiserings fejl: {fejl}"
        db.gem_fejl(ENHEDS_ID, 'Main', fejl_besked)
        raise RuntimeError(fejl_besked)


def start_webserver_blocking() -> NoReturn:

    try:
        from app import start_webserver
        
        db.gem_system_log(ENHEDS_ID, 'Main', 'Starter FastAPI webserver')
        print("Starter webserver - system nu fuldt operationelt")
        
        start_webserver()
        
    except Exception as fejl:
        fejl_besked = f"Webserver crash: {fejl}"
        db.gem_fejl(ENHEDS_ID, 'Main', fejl_besked)
        
        full_error = traceback.format_exc()
        db.gem_fejl(ENHEDS_ID, 'Main', full_error)
        
        sys.exit(1)


def main() -> NoReturn:

    global sensor_reference
    
    try:
        print("Registrerer signal handlers")
        registrer_signal_handlers()
        
        print("Starter automatisk vindues-styringssystem")
        db.gem_system_log(ENHEDS_ID, 'Main', '-' * 50)
        db.gem_system_log(ENHEDS_ID, 'Main', 'System startup initieret')
        db.gem_system_log(ENHEDS_ID, 'Main', '-' * 50)
        
        print("Initialiserer BME680 indoor sensor")
        sensor_reference = initialiser_bme680_sensor()
        
        print("Initialiserer MQTT client")
        initialiser_mqtt_klient()
        
        print("Initialiserer remote sync client")
        initialiser_sync_klient()
        
        start_webserver_blocking()
        
    except ImportError as fejl:
        fejl_besked = f"Import fejl - manglende dependency: {fejl}"
        db.gem_fejl(ENHEDS_ID, 'Main', fejl_besked)
        sys.exit(1)
    
    except RuntimeError as fejl:
        fejl_besked = f"Runtime fejl under startup: {fejl}"
        db.gem_fejl(ENHEDS_ID, 'Main', fejl_besked)
        sys.exit(1)
    
    except OSError as fejl:
        fejl_besked = f"OS fejl - hardware eller filesystem: {fejl}"
        db.gem_fejl(ENHEDS_ID, 'Main', fejl_besked)
        sys.exit(1)
    
    except Exception as fejl:
        fejl_besked = f"Uventet fejl under startup: {fejl}"
        db.gem_fejl(ENHEDS_ID, 'Main', fejl_besked)
        
        full_error = traceback.format_exc()
        db.gem_fejl(ENHEDS_ID, 'Main', full_error)
        
        sys.exit(1)


if __name__ == "__main__":

    main()